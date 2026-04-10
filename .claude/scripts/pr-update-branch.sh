#!/usr/bin/env bash
##############################################################################
# pr-update-branch.sh - Rebase a PR branch onto the latest base branch
#
# Fixes the "Cannot update this protected ref" / BLOCKED merge state caused
# by another PR merging after this one was created (stale branch).
#
# Usage:
#   bash .claude/scripts/pr-update-branch.sh [PR_NUMBER]
#
# If PR_NUMBER is omitted, detects from current branch.
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

DATA=$(gh pr view "$PR_NUMBER" --json mergeStateStatus,mergeable,headRefName,baseRefOid,baseRefName)
MERGE_STATE=$(echo "$DATA" | jq -r '.mergeStateStatus')
MERGEABLE=$(echo "$DATA" | jq -r '.mergeable')
HEAD_REF=$(echo "$DATA" | jq -r '.headRefName')
BASE_REF=$(echo "$DATA" | jq -r '.baseRefName')
PR_BASE_OID=$(echo "$DATA" | jq -r '.baseRefOid[0:8]')

MAIN_HEAD=$(git log "$BASE_REF" --oneline -1 2>/dev/null | awk '{print $1}' || echo "unknown")

echo "PR #$PR_NUMBER ($HEAD_REF → $BASE_REF)"
echo "  PR based on : $PR_BASE_OID"
echo "  $BASE_REF now at: $MAIN_HEAD"

if [[ "$PR_BASE_OID" == "$MAIN_HEAD" ]]; then
  echo "✅ Branch already up to date — nothing to do."
  exit 0
fi

if [[ "$MERGEABLE" == "CONFLICTING" ]]; then
  echo ""
  echo "❌ PR has merge conflicts — resolve them before updating branch."
  exit 1
fi

echo ""
echo "⚙️  Updating branch via GitHub API (rebase onto $BASE_REF)..."
gh pr update-branch "$PR_NUMBER"

echo "✅ Branch update queued."
echo ""
echo "GitHub will rebase $HEAD_REF onto $BASE_REF and trigger a new CI run."
echo "Check status with: bash .claude/scripts/pr-status.sh $PR_NUMBER"
