#!/usr/bin/env bash
##############################################################################
# create-github-issue.sh - Create a GitHub issue from CLI with standard formatting
#
# Used primarily for DEFER findings during PR review response: when a review
# comment is valid but out of scope for the current PR, create an issue and
# reply with the link.
#
# Usage:
#   bash .claude/scripts/create-github-issue.sh \
#     --title "Short issue title" \
#     [--body "Inline body text"]        # inline string
#     [--body-file PATH]                 # read body from file (preferred for long bodies)
#     [--body-stdin]                     # read body from stdin
#     [--label "bug"] \
#     [--label "enhancement"] \
#     [--pr PR_NUMBER]     # Adds "Deferred from PR #N" context to body
#
# Prints the created issue URL on success.
#
# Examples:
#   # Short inline body
#   bash .claude/scripts/create-github-issue.sh --title "Fix X" --body "Details here" --label bug
#
#   # Long body from a markdown file
#   bash .claude/scripts/create-github-issue.sh --title "Fix X" --body-file /tmp/issue-body.md --label bug
#
#   # Body from stdin (heredoc)
#   bash .claude/scripts/create-github-issue.sh --title "Fix X" --body-stdin --label bug <<'EOF'
#   ## Summary
#   Long body here...
#   EOF
##############################################################################

set -euo pipefail

TITLE=""
BODY=""
BODY_FILE=""
BODY_STDIN=false
LABELS=()
PR_NUMBER=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --title)      TITLE="$2";     shift 2 ;;
    --body)       BODY="$2";      shift 2 ;;
    --body-file)  BODY_FILE="$2"; shift 2 ;;
    --body-stdin) BODY_STDIN=true; shift ;;
    --label)      LABELS+=("$2"); shift 2 ;;
    --pr)         PR_NUMBER="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$TITLE" ]]; then
  echo "Error: --title is required." >&2
  echo "Usage: $0 --title \"...\" [--body \"...\"|--body-file PATH|--body-stdin] [--label bug] [--pr 123]" >&2
  exit 1
fi

# Resolve body from the three possible sources (precedence: file > stdin > inline)
if [[ -n "$BODY_FILE" ]]; then
  if [[ ! -f "$BODY_FILE" ]]; then
    echo "Error: --body-file not found: $BODY_FILE" >&2
    exit 1
  fi
  BODY=$(cat "$BODY_FILE")
elif [[ "$BODY_STDIN" == "true" ]]; then
  BODY=$(cat)
fi

if [[ -z "$BODY" ]]; then
  BODY="$TITLE"
fi

# Append deferred-from context if a PR number was given
if [[ -n "$PR_NUMBER" ]]; then
  if ! [[ "$PR_NUMBER" =~ ^[0-9]+$ ]]; then
    echo "Error: --pr must be a number." >&2
    exit 1
  fi
  BODY="${BODY}

---
_Deferred from PR #${PR_NUMBER} during code review._"
fi

# Build gh issue create args
GH_ARGS=(issue create --title "$TITLE" --body "$BODY")

for LABEL in "${LABELS[@]}"; do
  GH_ARGS+=(--label "$LABEL")
done

ISSUE_URL=$(gh "${GH_ARGS[@]}" 2>/dev/null)

echo "✅ Issue created: $ISSUE_URL"

# If deferred from a PR, print a ready-to-paste reply
if [[ -n "$PR_NUMBER" ]]; then
  ISSUE_NUMBER=$(echo "$ISSUE_URL" | grep -o '[0-9]*$')
  echo ""
  echo "Reply to paste on PR #$PR_NUMBER:"
  echo "---"
  echo "Valid point — deferred to issue #${ISSUE_NUMBER} for a follow-up PR."
  echo "---"
fi
