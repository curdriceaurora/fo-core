#!/bin/bash
# Canonical pre-PR guardrail orchestrator.
# This script does not define blocking policy itself. It runs the enforced
# layers that own guardrail policy:
#   - .pre-commit-config.yaml for staged-file/mechanical checks
#   - tests/ci for semantic guardrail checks
#   - .github/workflows/ci.yml for runtime support required in PR CI

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

# Declared up-front so ``set -u`` doesn't trip when ``collect_changed_files``
# produces an empty array (only possible when you run this script directly on
# ``main`` with a clean worktree, which isn't a supported scenario but
# shouldn't crash either). Older bash (macOS 3.2) treats an empty
# ``array[@]`` as unbound without this guard.
changed_files=()
changed_mode=""

run_step() {
  local label="$1"
  shift

  echo "▶ $label"
  "$@"
  echo "✓ $label"
  echo ""
}

print_files() {
  local mode="$1"
  shift

  if [[ "$#" -eq 0 ]]; then
    echo "  (none)"
    return
  fi

  echo "  Source: $mode"
  printf '  %s\n' "$@"
}

collect_changed_files() {
  # The script exists for PR validation — "what will land if this branch
  # merges to main". That means the interesting set is
  # ``git diff origin/main...HEAD`` (three-dot: files changed on this
  # branch since it diverged from main) *plus* any uncommitted /
  # untracked files in the working tree (the current edit session).
  #
  # This replaces the pre-2026-04-24 logic that compared the working tree
  # against HEAD. That logic produced an empty file list immediately
  # after a commit, which hid regressions on code that had just been
  # committed but not yet pushed — exactly when the validator is most
  # useful. (User feedback on PR #192.)
  local branch_files=()
  local working_tree_files=()
  local untracked_files=()
  changed_files=()

  local base="origin/main"
  if ! git rev-parse --verify "$base" >/dev/null 2>&1; then
    base="main"
  fi

  # Files committed on this branch since it diverged from the base.
  while IFS= read -r line; do [[ -n "$line" ]] && branch_files+=("$line"); done \
    < <(git diff --name-only --diff-filter=ACMR "${base}...HEAD" 2>/dev/null || true)

  # Uncommitted changes in the working tree (staged or unstaged).
  while IFS= read -r line; do [[ -n "$line" ]] && working_tree_files+=("$line"); done \
    < <(git diff --name-only --diff-filter=ACMR HEAD 2>/dev/null || true)

  # Untracked files (new files the user hasn't added yet).
  while IFS= read -r line; do [[ -n "$line" ]] && untracked_files+=("$line"); done \
    < <(git ls-files --others --exclude-standard)

  local combined=()
  [[ ${#branch_files[@]} -gt 0 ]] && combined+=("${branch_files[@]}")
  [[ ${#working_tree_files[@]} -gt 0 ]] && combined+=("${working_tree_files[@]}")
  [[ ${#untracked_files[@]} -gt 0 ]] && combined+=("${untracked_files[@]}")

  # Dedupe while preserving order.
  if [[ ${#combined[@]} -gt 0 ]]; then
    while IFS= read -r line; do changed_files+=("$line"); done \
      < <(printf '%s\n' "${combined[@]}" | awk '!seen[$0]++')
  fi

  changed_mode="branch diff vs $base + working tree + untracked"
}

collect_changed_files

echo "Pre-PR Guardrail Validation"
echo "==========================="
echo "Branch: $(git branch --show-current)"
echo ""
print_files "$changed_mode" "${changed_files[@]}"
echo ""

run_step "Validate pre-commit configuration" pre-commit validate-config

run_step "Run pre-commit on all files" pre-commit run --all-files

run_step "Run semantic CI guardrails" pytest tests/ci -q --no-cov --override-ini="addopts="

# ---------------------------------------------------------------------------
# Docstring coverage gate (fast, no optional deps)
# ---------------------------------------------------------------------------

echo "▶ Docstring coverage gate (≥95%)"
interrogate -v src/ --fail-under 95 -q
echo "✓ Docstring coverage"
echo ""

# ---------------------------------------------------------------------------
# Diff coverage gate (≥80% of changed lines must be covered)
# Mirrors CI step in .github/workflows/ci.yml lines 184-185.
# Skips gracefully when: not on a branch, no coverage.xml, or diff-cover
# is not installed.
# ---------------------------------------------------------------------------

CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || true)
if [[ -n "$CURRENT_BRANCH" ]] && command -v diff-cover &>/dev/null && [[ -f coverage.xml ]]; then
  echo "▶ Diff coverage gate (>=80% of changed lines)"
  git fetch origin main --quiet 2>/dev/null || true
  diff-cover coverage.xml --compare-branch=origin/main --fail-under=80
  echo "✓ Diff coverage gate"
  echo ""
else
  echo "ℹ Diff coverage gate skipped (not on a branch, no coverage.xml, or diff-cover not installed)."
  echo ""
fi

# ---------------------------------------------------------------------------
# Coverage floor gates (unit ≥93% branch, integration ≥30% branch) are
# enforced by CI on main-branch pushes — NOT here.
#
# Rationale (issue #914): the unit gate ran `pytest tests/ -m "unit"` (~80 s)
# and included optional-dep tests (rank-bm25, scikit-learn) that fail for
# anyone without [search] extras, creating spurious noise on every commit.
# Coverage is authoritative in ci.yml; local pre-commit stays fast & clean.
# ---------------------------------------------------------------------------

if git diff --cached -- '*.py' '.github/workflows/*.yml' 2>/dev/null | grep -q 'GITHUB_'; then
  echo "ℹ Detected GitHub-environment branching in staged changes."
  echo "  Guardrail tests in tests/ci must cover both local and PR CI contexts."
  echo ""
fi

echo "Guardrail validation passed."
