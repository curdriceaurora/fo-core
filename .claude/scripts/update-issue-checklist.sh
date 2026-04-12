#!/usr/bin/env bash
# update-issue-checklist.sh — Check off modules in a GitHub issue checklist.
#
# Usage:
#   bash .claude/scripts/update-issue-checklist.sh <ISSUE_NUM> <MODULE_PATH> [NEW_COVERAGE]
#   bash .claude/scripts/update-issue-checklist.sh <ISSUE_NUM> --file <PAIRS_FILE>
#
# Single module:
#   bash .claude/scripts/update-issue-checklist.sh 59 "utils/readers/cad.py" "91%"
#   bash .claude/scripts/update-issue-checklist.sh 59 "utils/readers/cad.py"
#
# Batch from file (one "module_path new_coverage%" per line):
#   bash .claude/scripts/update-issue-checklist.sh 59 --file /tmp/completed.txt
#
# The script:
#   1. Fetches the current issue body
#   2. Replaces "- [ ] `<path>` (<old_pct>%)" with "- [x] `<path>` (→ <NEW_COVERAGE>)"
#      or just "- [x] `<path>` (...)" if no new coverage provided
#   3. Updates the issue via gh CLI
set -euo pipefail

ISSUE_NUM="${1:?Usage: update-issue-checklist.sh <ISSUE_NUM> <MODULE_PATH> [NEW_COVERAGE]}"
BODY_TMP="$(mktemp /tmp/issue_body.XXXXXX.md)"
trap 'rm -f "$BODY_TMP"' EXIT

gh issue view "$ISSUE_NUM" --json body -q '.body' > "$BODY_TMP"

check_off_module() {
    local module_path="$1"
    local new_coverage="${2:-}"
    local escaped_path
    escaped_path="$(printf '%s' "$module_path" | sed 's|/|\\/|g' | sed 's|\.|\\.|g')"

    if [ -n "$new_coverage" ]; then
        sed -i '' "s|- \[ \] \`${escaped_path}\` ([^)]*%)|- [x] \`${module_path}\` (→ ${new_coverage})|g" "$BODY_TMP"
    else
        sed -i '' "s|- \[ \] \`${escaped_path}\` ([^)]*%)|- [x] \`${module_path}\` (→ ≥80%)|g" "$BODY_TMP"
    fi
}

if [ "${2:-}" = "--file" ]; then
    PAIRS_FILE="${3:?--file requires a path argument}"
    while IFS=' ' read -r module_path new_cov || [ -n "$module_path" ]; do
        [ -z "$module_path" ] && continue
        check_off_module "$module_path" "$new_cov"
    done < "$PAIRS_FILE"
else
    MODULE_PATH="${2:?Usage: update-issue-checklist.sh <ISSUE_NUM> <MODULE_PATH> [NEW_COVERAGE]}"
    NEW_COV="${3:-}"
    check_off_module "$MODULE_PATH" "$NEW_COV"
fi

gh issue edit "$ISSUE_NUM" --body-file "$BODY_TMP"
echo "Updated issue #${ISSUE_NUM} checklist."
