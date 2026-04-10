#!/usr/bin/env bash
set -euo pipefail

WORKTREE_PATH="${1:?Usage: worktree-to-pr.sh <worktree-path> <branch-name> <issue-number> <pr-title> <pr-body-file>}"
BRANCH_NAME="${2:?Missing branch name}"
ISSUE_NUMBER="${3:?Missing issue number}"
PR_TITLE="${4:?Missing PR title}"
PR_BODY_FILE="${5:?Missing PR body file path}"

REPO_ROOT="$(git -C "$WORKTREE_PATH" rev-parse --show-toplevel)"
WORKTREE_BRANCH="$(git -C "$WORKTREE_PATH" branch --show-current)"

git -C "$WORKTREE_PATH" branch -m "$WORKTREE_BRANCH" "$BRANCH_NAME" 2>/dev/null || true

git -C "$WORKTREE_PATH" push -u origin "$BRANCH_NAME"

PR_BODY="$(cat "$PR_BODY_FILE")"
gh pr create \
  --repo curdriceaurora/fo-core \
  --head "$BRANCH_NAME" \
  --base main \
  --title "$PR_TITLE" \
  --body "$PR_BODY"
