#!/usr/bin/env bash
##############################################################################
# issue-to-branch.sh - Create a feature branch from a GitHub issue number
#
# Fetches the issue title, slugifies it, and creates a branch named:
#   feature/issue-<NUMBER>-<slug>
#
# Usage:
#   bash .claude/scripts/issue-to-branch.sh <ISSUE_NUMBER>
#
# Example:
#   bash .claude/scripts/issue-to-branch.sh 123
#   → Creates and checks out: feature/issue-123-add-search-index
##############################################################################

set -euo pipefail

ISSUE_NUMBER="${1:?Usage: $0 <ISSUE_NUMBER>}"

if ! [[ "$ISSUE_NUMBER" =~ ^[0-9]+$ ]]; then
  echo "Error: invalid issue number: $ISSUE_NUMBER" >&2
  exit 1
fi

# Fetch issue title
TITLE=$(gh issue view "$ISSUE_NUMBER" --json title -q '.title' 2>/dev/null || true)
if [[ -z "$TITLE" ]]; then
  echo "Error: issue #$ISSUE_NUMBER not found." >&2
  exit 1
fi

# Slugify: lowercase, replace non-alphanumeric with hyphens, collapse runs, trim
SLUG=$(echo "$TITLE" \
  | tr '[:upper:]' '[:lower:]' \
  | sed 's/[^a-z0-9]/-/g' \
  | sed 's/-\{2,\}/-/g' \
  | sed 's/^-//;s/-$//' \
  | cut -c1-50)

BRANCH="feature/issue-${ISSUE_NUMBER}-${SLUG}"

# Ensure we're on main and up to date
CURRENT=$(git branch --show-current)
if [[ "$CURRENT" != "main" ]]; then
  echo "Switching to main first..."
  git checkout main
fi

git fetch origin main --quiet
git merge --ff-only origin/main --quiet

# Create branch
git checkout -b "$BRANCH"

echo "✅ Created branch: $BRANCH"
echo ""
echo "Issue #$ISSUE_NUMBER: $TITLE"
echo ""
echo "Next steps:"
echo "  1. Implement the fix"
echo "  2. Run: bash .claude/scripts/pre-commit-validation.sh"
echo "  3. Commit and run: bash .claude/scripts/worktree-to-pr.sh (if in worktree)"
echo "     Or: gh pr create --title \"...\" --body \"Closes #$ISSUE_NUMBER\""
