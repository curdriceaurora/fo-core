#!/usr/bin/env bash
##############################################################################
# jsonl-to-markdown.sh - Render harvested PR comment JSONL as markdown digest.
#
# Reads JSONL from stdin (one row per comment) and writes a markdown digest
# grouped by PR, with sub-sections for unresolved threads, resolved threads,
# PR-level comments, and review summaries. Useful for hand-off to triage
# agents that need a human-readable corpus.
#
# Usage:
#   bash .claude/scripts/jsonl-to-markdown.sh < tasks/pr-comments-271-plus.jsonl \
#       > tasks/pr-comments-271-plus.md
##############################################################################

set -euo pipefail

if ! command -v jq &>/dev/null; then
    echo "Error: jq not found" >&2
    exit 1
fi

jq -s '
  group_by(.pr)
  | map({pr: .[0].pr, title: .[0].pr_title, rows: .})
  | sort_by(.pr)
  | .[]
' | jq -r '
  "## PR #\(.pr) — \(.title // "?")\n",
  (
    [.rows[] | select(.kind=="thread" and .is_resolved==false)]
    | if length > 0 then
        "### ❌ Unresolved threads (\(length))\n",
        (.[] | "- **\(.author // "?")** at `\(.path // "n/a"):\(.line // "?")` (thread `\(.thread_id)`):\n  \((.body // "") | gsub("\n"; "\n  ") | .[0:1500])\n")
      else empty end
  ),
  (
    [.rows[] | select(.kind=="thread" and .is_resolved==true)]
    | if length > 0 then
        "\n### ✅ Resolved threads (\(length))\n",
        (.[] | "- **\(.author // "?")** at `\(.path // "n/a"):\(.line // "?")`: \((.body // "") | gsub("\n"; " ") | .[0:200])")
      else empty end
  ),
  "\n---\n"
'
