#!/usr/bin/env bash
##############################################################################
# harvest-pr-comments.sh - Aggregate review threads + comments across PRs.
#
# Emits one JSON object per line to stdout. Each row is a single review-thread
# comment with thread resolution status, author, path, line, and body. Rows for
# PR-level comments and review summary bodies are also included (with
# thread=null).
#
# Usage:
#   bash .claude/scripts/harvest-pr-comments.sh <PR1> [PR2 ...]
#   bash .claude/scripts/harvest-pr-comments.sh --min 271
#
# With --min, the script fetches every closed/merged PR with number >= N.
##############################################################################

set -euo pipefail

if ! command -v jq &>/dev/null; then
    echo "Error: jq not found" >&2
    exit 1
fi

REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
OWNER="${REPO%%/*}"
NAME="${REPO##*/}"

prs=()
if [[ "${1:-}" == "--min" ]]; then
    min="${2:-}"
    if ! [[ "$min" =~ ^[0-9]+$ ]]; then
        echo "Error: --min requires a numeric value" >&2
        exit 1
    fi
    mapfile -t prs < <(
        gh pr list --state all --limit 200 --json number \
            | jq -r --argjson m "$min" '[.[] | select(.number >= $m)] | sort_by(.number) | .[].number'
    )
else
    prs=("$@")
fi

if [[ ${#prs[@]} -eq 0 ]]; then
    echo "Error: no PR numbers supplied" >&2
    exit 1
fi

QUERY='query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      title
      mergedAt
      state
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          isOutdated
          comments(first: 50) {
            nodes {
              author { login }
              path
              line
              originalLine
              body
              createdAt
            }
          }
        }
      }
      comments(first: 50) {
        nodes {
          author { login }
          createdAt
          body
        }
      }
      reviews(first: 50) {
        nodes {
          author { login }
          state
          submittedAt
          body
        }
      }
    }
  }
}'

for pr in "${prs[@]}"; do
    raw=$(gh api graphql \
        --field owner="$OWNER" \
        --field repo="$NAME" \
        --field number="$pr" \
        --raw-field query="$QUERY" 2>/dev/null \
        || echo '{}')

    # Emit one row per thread *comment* (not per thread) so follow-up
     # replies that refine or replace the original concern are preserved.
     # ``comment_index`` is 0-based within the thread; the first row per
     # thread carries the originating concern, subsequent rows are replies.
    echo "$raw" | jq -c --argjson pr "$pr" '
        .data.repository.pullRequest as $p
        | $p.reviewThreads.nodes[]? as $t
        | $t.comments.nodes
        | to_entries[]
        | .key as $i
        | .value as $c
        | {
            pr: $pr,
            pr_title: $p.title,
            pr_state: $p.state,
            kind: "thread",
            thread_id: $t.id,
            is_resolved: $t.isResolved,
            is_outdated: $t.isOutdated,
            comment_index: $i,
            author: ($c.author.login // null),
            path: $c.path,
            line: ($c.line // $c.originalLine),
            created_at: $c.createdAt,
            body: $c.body
          }
    '
    echo "$raw" | jq -c --argjson pr "$pr" '
        .data.repository.pullRequest as $p
        | $p.comments.nodes[]?
        | {
            pr: $pr,
            pr_title: $p.title,
            pr_state: $p.state,
            kind: "pr_comment",
            thread_id: null,
            is_resolved: null,
            is_outdated: null,
            author: (.author.login // null),
            path: null,
            line: null,
            created_at: .createdAt,
            body: .body
          }
    '
    echo "$raw" | jq -c --argjson pr "$pr" '
        .data.repository.pullRequest as $p
        | $p.reviews.nodes[]?
        | select((.body // "") != "")
        | {
            pr: $pr,
            pr_title: $p.title,
            pr_state: $p.state,
            kind: "review",
            thread_id: null,
            is_resolved: null,
            is_outdated: null,
            author: (.author.login // null),
            path: null,
            line: null,
            state: .state,
            created_at: .submittedAt,
            body: .body
          }
    '
done
