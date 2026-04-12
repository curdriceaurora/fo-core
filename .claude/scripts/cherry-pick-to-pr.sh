#!/usr/bin/env bash
# cherry-pick-to-pr.sh — Cherry-pick one or more commits onto a new branch from
# main, push it, and create a GitHub PR.
#
# Usage:
#   bash .claude/scripts/cherry-pick-to-pr.sh \
#     <new-branch> <pr-title> <pr-body-file> <commit> [<commit> ...]
#
# Example:
#   bash .claude/scripts/cherry-pick-to-pr.sh \
#     coverage/59-buckets-b-through-e \
#     "test(integration): Buckets B-E coverage uplift" \
#     /tmp/pr_body.md \
#     a960988
set -euo pipefail

if ! command -v gh &>/dev/null; then
    echo "Error: gh CLI not found. Install it from https://cli.github.com" >&2
    exit 1
fi

NEW_BRANCH="${1:?Usage: cherry-pick-to-pr.sh <new-branch> <pr-title> <pr-body-file> <commit>...}"
PR_TITLE="${2:?Missing PR title}"
PR_BODY_FILE="${3:?Missing PR body file}"
shift 3
COMMITS=("$@")

if [ "${#COMMITS[@]}" -eq 0 ]; then
    echo "Error: at least one commit SHA is required" >&2
    exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
CURRENT_BRANCH="$(git branch --show-current)"

if git show-ref --verify --quiet "refs/heads/$NEW_BRANCH"; then
    echo "Error: branch '$NEW_BRANCH' already exists locally." >&2
    echo "  Delete it first: git branch -D $NEW_BRANCH" >&2
    exit 1
fi

git fetch origin main
git checkout origin/main -b "$NEW_BRANCH"

for sha in "${COMMITS[@]}"; do
    git cherry-pick "$sha"
done

git push -u origin "$NEW_BRANCH"

gh pr create \
    --repo "$(gh repo view --json nameWithOwner -q '.nameWithOwner')" \
    --head "$NEW_BRANCH" \
    --base main \
    --title "$PR_TITLE" \
    --body-file "$PR_BODY_FILE"

git checkout "$CURRENT_BRANCH"
