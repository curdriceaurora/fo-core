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
    # Use a cap large enough to cover any reasonable audit, then warn
    # if we hit it (codex PR #329 thread PRRT_kwDOR_Rkws6DzN0B —
    # ``--limit 200`` was silently truncating). 5000 covers fo-core
    # 50× over; anything closer to that limit deserves manual paging.
    pr_limit=5000
    pr_list_raw=$(gh pr list --state all --limit "$pr_limit" --json number)
    if [[ -z "$pr_list_raw" ]]; then
        echo "Error: gh pr list returned no data" >&2
        exit 1
    fi
    pr_list_count=$(echo "$pr_list_raw" | jq 'length')
    if [[ "$pr_list_count" -ge "$pr_limit" ]]; then
        echo "Warning: gh pr list returned $pr_list_count PRs (hit --limit $pr_limit cap)." >&2
        echo "         Older PRs in the >=$min range may be missing." >&2
        echo "         Pass PR numbers explicitly to override." >&2
    fi
    mapfile -t prs < <(
        echo "$pr_list_raw" \
            | jq -r --argjson m "$min" '[.[] | select(.number >= $m)] | sort_by(.number) | .[].number'
    )
else
    prs=("$@")
fi

if [[ ${#prs[@]} -eq 0 ]]; then
    echo "Error: no PR numbers supplied" >&2
    exit 1
fi

# Single-page query (used for the first request and re-used with a cursor).
# Each cursor-bearing field paginates independently; pass null on the first
# call, then the previous response's ``endCursor`` until ``hasNextPage`` is
# false. This avoids silently truncating long PRs (codex PR #329 thread
# PRRT_kwDOR_Rkws6DyFj4 — issue surfaces on any PR with >100 threads or >50
# comments/reviews).
# Variable names match the connection names exactly so that the
# ``paginate_connection`` helper can derive the cursor param via
# ``${conn}Cursor`` without a name-mismatch bug. (Round-3 of this script
# named the threads variable ``threadsCursor`` but sent it as
# ``reviewThreadsCursor``, so the cursor never advanced and PRs with
# >100 threads would loop forever — codex thread PRRT_kwDOR_Rkws6Dy_8l.)
QUERY='query($owner: String!, $repo: String!, $number: Int!,
            $reviewThreadsCursor: String,
            $commentsCursor: String,
            $reviewsCursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      title
      mergedAt
      state
      reviewThreads(first: 100, after: $reviewThreadsCursor) {
        pageInfo { hasNextPage endCursor }
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
      comments(first: 50, after: $commentsCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          author { login }
          createdAt
          body
        }
      }
      reviews(first: 50, after: $reviewsCursor) {
        pageInfo { hasNextPage endCursor }
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

# Paginate one connection until exhausted, returning the merged JSON across
# all pages. Re-uses QUERY but only consumes pages of the named connection.
# ``$1`` = pr number, ``$2`` = connection name (reviewThreads / comments /
# reviews). Emits one ``data.repository.pullRequest.<conn>.nodes[]`` per
# page; the caller jq-merges via ``[ inputs ]``.
paginate_connection() {
    local pr="$1"
    local conn="$2"
    # Variable name in the query is exactly ``${conn}Cursor`` (e.g.
    # ``reviewThreadsCursor`` for the ``reviewThreads`` connection).
    local var="${conn}Cursor"
    local cursor=""  # empty == null in gh api -F shorthand
    local response has_next stderr_file rc
    stderr_file=$(mktemp)
    trap 'rm -f "$stderr_file"' RETURN
    while :; do
        # Fail fast on transient API / auth / GraphQL errors instead of
        # silently emitting an empty page (codex PR #329 thread
        # PRRT_kwDOR_Rkws6DzNz-). The previous ``|| echo '{}'`` swallowed
        # every failure and let the script exit successfully with an
        # incomplete audit corpus.
        if response=$(gh api graphql --raw-field query="$QUERY" \
                -F owner="$OWNER" -F repo="$NAME" -F number="$pr" \
                -F "${var}=${cursor}" 2>"$stderr_file"); then
            rc=0
        else
            rc=$?
        fi
        if [[ "$rc" -ne 0 ]] || [[ -z "$response" ]]; then
            echo "Error: gh api graphql failed (rc=$rc) for PR #$pr / $conn" >&2
            sed 's/^/  /' "$stderr_file" >&2
            return 1
        fi
        # GraphQL "data: null + errors: [...]" is a partial-failure shape
        # that ``--paginate``-style fetches must surface, not skip.
        if echo "$response" | jq -e '(.errors | length // 0) > 0' >/dev/null 2>&1; then
            echo "Error: GraphQL returned errors for PR #$pr / $conn:" >&2
            echo "$response" | jq -c '.errors' >&2
            return 1
        fi
        echo "$response" | jq -c --arg conn "$conn" \
            '.data.repository.pullRequest[$conn].nodes // []'
        has_next=$(echo "$response" | jq -r --arg conn "$conn" \
            '.data.repository.pullRequest[$conn].pageInfo.hasNextPage // false')
        if [[ "$has_next" != "true" ]]; then
            break
        fi
        cursor=$(echo "$response" | jq -r --arg conn "$conn" \
            '.data.repository.pullRequest[$conn].pageInfo.endCursor // empty')
        if [[ -z "$cursor" ]]; then
            break
        fi
    done
}

# Fetch the PR title + state once via a tiny query (independent of pagination).
fetch_meta() {
    local pr="$1"
    gh api graphql --raw-field query='
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            pullRequest(number: $number) { title state }
          }
        }' \
        -F owner="$OWNER" -F repo="$NAME" -F number="$pr" 2>/dev/null \
        | jq -c '.data.repository.pullRequest // {title: null, state: null}'
}

for pr in "${prs[@]}"; do
    meta=$(fetch_meta "$pr")
    # Aggregate all pages for each connection via [inputs] in jq.
    threads_json=$(paginate_connection "$pr" reviewThreads | jq -cs 'add // []')
    pr_comments_json=$(paginate_connection "$pr" comments | jq -cs 'add // []')
    reviews_json=$(paginate_connection "$pr" reviews | jq -cs 'add // []')

    # Stitch into the same shape the old single-query response had so the
    # downstream jq emit blocks below don't need to change.
    raw=$(jq -cn \
        --argjson meta "$meta" \
        --argjson threads "$threads_json" \
        --argjson comments "$pr_comments_json" \
        --argjson reviews "$reviews_json" \
        '{data: {repository: {pullRequest: {
            title: $meta.title,
            state: $meta.state,
            reviewThreads: {nodes: $threads},
            comments: {nodes: $comments},
            reviews: {nodes: $reviews}
        }}}}')

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
