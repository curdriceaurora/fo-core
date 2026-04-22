#!/usr/bin/env bash
# check-stale-doc-refs.sh — Detect documentation references to files/classes that no longer exist.
# Run: bash .claude/scripts/check-stale-doc-refs.sh [--staged-only]
# Exit code 0 = clean, 1 = stale references found

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
STAGED_ONLY=false
ERRORS=0

if [[ "${1:-}" == "--staged-only" ]]; then
  STAGED_ONLY=true
fi

# Collect markdown files to check
if $STAGED_ONLY; then
  MD_FILES=$(git diff --cached --name-only --diff-filter=ACM -- '*.md' || true)
else
  MD_FILES=$(git -C "$REPO_ROOT" ls-files '*.md' \
    | grep -v -E '^(docs/plans/|\.claude/worktrees/)')
fi

if [[ -z "$MD_FILES" ]]; then
  echo "✓ No markdown files to check"
  exit 0
fi

echo "🔍 Checking documentation references..."
echo ""

# --- Check 1: Source file references ---
# Match patterns like src/foo/bar.py or interfaces/engine.py
while IFS= read -r md_file; do
  [[ -z "$md_file" ]] && continue
  full_path="$REPO_ROOT/$md_file"
  [[ -f "$full_path" ]] || continue

  # Extract Python file references (src/... paths ending in .py)
  # Skip lines inside code blocks (examples/templates) — they use placeholder paths
  while IFS=: read -r line_num line_content; do
    # Skip lines that look like examples (contain my_service, your_, example_, etc.)
    if echo "$line_content" | grep -qE 'my_service|your_|example_|placeholder'; then
      continue
    fi
    # Pull out src/...py references
    refs=$(echo "$line_content" | grep -oE 'src/[a-zA-Z0-9_/]+\.py' || true)
    for ref in $refs; do
      if [[ ! -f "$REPO_ROOT/$ref" ]]; then
        echo "  ❌ $md_file:$line_num → $ref (FILE MISSING)"
        ERRORS=$((ERRORS + 1))
      fi
    done
  done < <(grep -n 'src/[a-zA-Z0-9_/]*\.py' "$full_path" 2>/dev/null || true)
done <<< "$MD_FILES"

# --- Check 2: Class/Protocol references that were known-deleted ---
# Maintain a list of intentionally deleted symbols + their old locations
DELETED_SYMBOLS=(
  "EngineProtocol:interfaces/engine.py"
)

for entry in "${DELETED_SYMBOLS[@]}"; do
  symbol="${entry%%:*}"
  old_file="${entry##*:}"

  while IFS= read -r md_file; do
    [[ -z "$md_file" ]] && continue
    full_path="$REPO_ROOT/$md_file"
    [[ -f "$full_path" ]] || continue

    hits=$(grep -n "$symbol" "$full_path" 2>/dev/null | grep -v "deleted\|DELETED\|removed\|Removed\|zero implementors\|deferred\|Deferred" || true)
    if [[ -n "$hits" ]]; then
      while IFS= read -r hit; do
        line_num=$(echo "$hit" | cut -d: -f1)
        echo "  ⚠️  $md_file:$line_num → references deleted symbol '$symbol' (was in $old_file)"
        ERRORS=$((ERRORS + 1))
      done <<< "$hits"
    fi
  done <<< "$MD_FILES"
done

# --- Check 3: Method references that don't exist ---
# Known false method references (add as discovered)
FALSE_METHODS=(
  "ConfigManager.get_allowed_dirs"
)

for method in "${FALSE_METHODS[@]}"; do
  class_name="${method%%.*}"
  method_name="${method##*.}"

  while IFS= read -r md_file; do
    [[ -z "$md_file" ]] && continue
    full_path="$REPO_ROOT/$md_file"
    [[ -f "$full_path" ]] || continue

    hits=$(grep -n "$method" "$full_path" 2>/dev/null || true)
    if [[ -n "$hits" ]]; then
      # Verify method still doesn't exist
      if ! grep -rq "def $method_name" "$REPO_ROOT/src/" 2>/dev/null; then
        while IFS= read -r hit; do
          line_num=$(echo "$hit" | cut -d: -f1)
          echo "  ❌ $md_file:$line_num → references non-existent method '$method'"
          ERRORS=$((ERRORS + 1))
        done <<< "$hits"
      fi
    fi
  done <<< "$MD_FILES"
done

# --- Check 4: Removed features / terminology drift ---
# Each entry: "grep-E-pattern:reason". Patterns use POSIX-extended regex
# (as accepted by `grep -E`).
#
# NOTE: `reason` must not contain a literal ':' — the split is
# ${entry%%:*} / ${entry#*:}, so a colon in the reason truncates it.
#
# Exempt lines: fenced code blocks, and lines explicitly documenting the
# change in history (e.g. "was removed", "renamed to X", "deprecated").
# The exemption regex below is deliberately narrow — using bare "was\b"
# would exempt almost any prose paragraph.
REMOVED_TERMS=(
  '\[parsers\]:the "parsers" extra was removed; valid extras are dev cloud llama mlx claude audio video dedup archive scientific cad build search all'
  '\[gui\]:fo-core is CLI-only; no [gui] extra exists'
  '/plugins/:the plugin system was removed; fo-core has no plugin layer'
  '\bFastAPI\b:fo-core is CLI-only; no FastAPI/uvicorn web server exists'
  '\buvicorn\b:fo-core is CLI-only; no FastAPI/uvicorn web server exists'
  '\bollama ls\b:correct command is "ollama list" (not "ollama ls")'
  '\bmarketplace\b:the marketplace CLI command was removed'
)

# Matches history-context phrases only — not every sentence that uses "was".
HISTORY_EXEMPT_REGEX='\b(was|were|is|are) (removed|deprecated|renamed|replaced|deleted|dropped|retired)\b|\bno longer\b|\bformerly\b|\bpreviously\b'

for entry in "${REMOVED_TERMS[@]}"; do
  pattern="${entry%%:*}"
  reason="${entry#*:}"

  while IFS= read -r md_file; do
    [[ -z "$md_file" ]] && continue
    full_path="$REPO_ROOT/$md_file"
    [[ -f "$full_path" ]] || continue

    if $STAGED_ONLY; then
      # Fence- and line-identity-aware staged-only scan.
      #
      # 1. Compute line numbers in the post-staged file that are NOT inside a
      #    fenced block.  Fence openers per CommonMark: 0-3 spaces of indent,
      #    then 3+ backticks or 3+ tildes.  Previous regex matched only
      #    exactly three backticks at column 0, missing indented fences,
      #    tilde fences (`~~~`), and longer-backtick fences (` ```` `).
      # 2. Parse the `@@ -L,N +M,P @@` hunk headers to get the set of added
      #    line numbers in the post-staged file.
      # 3. Intersect the two sets to get "added prose lines".  Previous
      #    approach intersected by raw text (`grep -Fxf`), which lost line
      #    identity: an added fenced line whose content happened to match
      #    unchanged prose was wrongly flagged.  (Codex PR #144 threads.)
      non_fenced_linenos=$(git show ":$md_file" 2>/dev/null | awk '
        /^[[:space:]]{0,3}(`{3,}|~{3,})/ { in_fence = !in_fence; next }
        !in_fence { print NR }
      ' || true)
      added_linenos=$(git diff --cached --unified=0 -- "$md_file" 2>/dev/null | awk '
        /^@@/ {
          if (match($0, /\+[0-9]+(,[0-9]+)?/)) {
            spec = substr($0, RSTART + 1, RLENGTH - 1)
            split(spec, parts, ",")
            start = parts[1] + 0
            count = (length(parts) > 1) ? (parts[2] + 0) : 1
            for (i = 0; i < count; i++) print start + i
          }
        }
      ' || true)
      if [[ -z "$non_fenced_linenos" || -z "$added_linenos" ]]; then
        hits=""
      else
        # Hashmap-based set intersection — order-independent, avoids the
        # numeric-vs-lexical sort pitfall that `comm` would hit.
        prose_added_linenos=$(awk 'NR==FNR { seen[$0]=1; next } $0 in seen' \
          <(echo "$non_fenced_linenos") <(echo "$added_linenos"))
        if [[ -z "$prose_added_linenos" ]]; then
          hits=""
        else
          # Space-separate line numbers before passing via `-v`: BSD awk warns
          # on embedded newlines in `-v` values.
          prose_added_linenos_str=$(echo "$prose_added_linenos" | tr '\n' ' ')
          prose_added=$(git show ":$md_file" 2>/dev/null | awk -v LINES="$prose_added_linenos_str" '
            BEGIN { n = split(LINES, arr, " "); for (i = 1; i <= n; i++) if (arr[i] != "") want[arr[i]+0] = 1 }
            want[NR] { print }
          ' || true)
          hits=$(echo "$prose_added" \
            | grep -v -E "$HISTORY_EXEMPT_REGEX" \
            | grep -E "$pattern" || true)
        fi
      fi
      if [[ -n "$hits" ]]; then
        echo "  ⚠️  $md_file → newly added line matches stale pattern '$pattern' ($reason)"
        ERRORS=$((ERRORS + 1))
      fi
    else
      # Full-scan mode: line-numbered output across the whole file.
      # After `cat -n`, content starts after the tab; accept 0-3 spaces of
      # indent and 3+ backticks or 3+ tildes as a fence marker.
      hits=$(cat -n "$full_path" 2>/dev/null \
        | awk '/^[[:space:]]*[0-9]+\t[[:space:]]{0,3}(`{3,}|~{3,})/ { in_fence = !in_fence; next } !in_fence' \
        | grep -v -E "$HISTORY_EXEMPT_REGEX" \
        | grep -E "$pattern" || true)
      if [[ -n "$hits" ]]; then
        while IFS= read -r hit; do
          line_num=$(echo "$hit" | awk '{print $1}')
          echo "  ⚠️  $md_file:$line_num → stale reference matching '$pattern' ($reason)"
          ERRORS=$((ERRORS + 1))
        done <<< "$hits"
      fi
    fi
  done <<< "$MD_FILES"
done

echo ""
if [[ $ERRORS -gt 0 ]]; then
  echo "❌ Found $ERRORS stale reference(s) in documentation"
  echo "   Fix these before committing, or add to DELETED_SYMBOLS/FALSE_METHODS/REMOVED_TERMS if intentional."
  exit 1
else
  echo "✓ No stale documentation references found"
  exit 0
fi
