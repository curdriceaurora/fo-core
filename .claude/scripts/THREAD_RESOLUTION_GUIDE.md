# PR Thread Resolution Guide

Automated script for resolving GitHub PR comment threads with optional replies.

## Quick Start

### Resolve all unresolved threads (no replies)
```bash
.claude/scripts/resolve-pr-threads.sh 627
```

### Resolve threads with replies
```bash
.claude/scripts/resolve-pr-threads.sh 627 --replies replies.json
```

### Preview changes without making them (dry-run)
```bash
.claude/scripts/resolve-pr-threads.sh 627 --dry-run
.claude/scripts/resolve-pr-threads.sh 627 --replies replies.json --dry-run
```

## Replies File Format

### Option 1: JSON Object (thread ID → reply text)

**Best for**: Targeting specific threads with specific replies

```json
{
  "PRRT_kwDOQ97U-c5yw18Q": "✅ Fixed: Removed this no-op test...",
  "PRRT_kwDOQ97U-c5yw18U": "✅ Fixed: Differentiated the test...",
  "PRRT_kwDOQ97U-c5yw18V": "✅ Fixed: Simplified the signature..."
}
```

### Option 2: JSON Array (replies in order)

**Best for**: Replying to all threads in sequence

```json
[
  "✅ Fixed: First reply",
  "✅ Fixed: Second reply",
  "✅ Fixed: Third reply"
]
```

## Workflow Integration with PR Review Response Protocol

This script automates **Step 6** of the PR Review Response Protocol:

```
Step 4: Run quality gates
Step 5: Commit and push

Step 6: Resolve comments
  ├─ For each APPLY finding: Reply with fix explanation
  ├─ For each SKIP finding: Reply with reason
  ├─ For each CLARIFY finding: Reply with clarification
  ├─ For each DEFER finding: Reply with GitHub issue link
  └─ Then resolve all threads
```

### Usage in PR Review Workflow

```bash
# 1. Extract all findings and apply fixes locally
# 2. Commit and push
git push origin my-branch

# 3. Add replies and resolve all threads at once
.claude/scripts/resolve-pr-threads.sh 627 --replies my-replies.json

# 4. PR is now ready with all threads resolved
```

## Examples

### Example 1: Simple resolution with no replies
```bash
# Just resolve all threads
.claude/scripts/resolve-pr-threads.sh 627

# Output:
# 🔍 Fetching PR #627...
# 📋 Fetching unresolved threads...
# Found 5 unresolved thread(s)
# [1/5] Thread: PRRT_kwDOQ97U-c5yw18Q
#   ↳ Resolving thread...
#   ✓ Resolved
# ...
# ✅ Resolved: 5 threads
```

### Example 2: Resolve with replies
```bash
# Create replies.json with your responses
cat > replies.json << 'EOF'
{
  "PRRT_kwDOQ97U-c5yw18Q": "✅ Fixed: Removed no-op test as it became meaningless after previous fixes.",
  "PRRT_kwDOQ97U-c5yw18U": "✅ Fixed: Differentiated tests to cover different endpoints.",
  "PRRT_kwDOQ97U-c5yw18V": "✅ Fixed: Simplified test signature to remove unused parameters.",
  "PRRT_kwDOQ97U-c5yw18X": "✅ Fixed: Clarified Code Review is Claude Code-only.",
  "PRRT_kwDOQ97U-c5yw18Y": "✅ Fixed: Added Claude Code-only annotations to tool commands."
}
EOF

# Run with replies
.claude/scripts/resolve-pr-threads.sh 627 --replies replies.json

# Output:
# 🔍 Fetching PR #627...
# 📋 Fetching unresolved threads...
# 📄 Loading replies from: replies.json
# Found 5 unresolved thread(s)
# [1/5] Thread: PRRT_kwDOQ97U-c5yw18Q
#   ↳ Adding reply: ✅ Fixed: Removed no-op test...
#   ✓ Reply added
#   ↳ Resolving thread...
#   ✓ Resolved
# ...
# ✅ Resolved: 5 threads
```

### Example 3: Dry-run preview
```bash
# Preview what would be resolved
.claude/scripts/resolve-pr-threads.sh 627 --dry-run

# Output shows what would happen without making changes
# [DRY RUN] Would resolve: 5 threads
```

## What The Script Does

1. **Validates** the PR number and checks if PR exists
2. **Fetches** all unresolved review threads from GitHub
3. **Loads** replies if provided (optional)
4. **Adds replies** to each thread (if replies file provided)
5. **Resolves** each thread via GraphQL mutation
6. **Reports** summary of resolved threads

## Error Handling

- ❌ Invalid PR number → Exit with error message
- ❌ PR not found → Exit with error message
- ❌ Replies file not found → Exit with error message
- ⚠️ Failed to add reply → Continue to resolve thread, report warning
- ⚠️ Failed to resolve thread → Report warning, continue

## Advanced: Generating Replies Automatically

You can generate the replies.json file programmatically:

```bash
# Extract Copilot findings and create responses
gh pr view 627 --json comments | jq '.comments[] | select(.author.login == "copilot-pull-request-reviewer") | .body' | \
  sed 's/^/✅ Fixed: /' > replies-raw.txt

# Then manually format into JSON object
```

Or create a helper script that parses findings and generates structured replies.

## Troubleshooting

### Script says "No unresolved threads"
- This is normal! Means all threads are already resolved
- Or all threads were dismissed/resolved via GitHub UI

### "Failed to add reply" warning
- Check reply text for special characters that need escaping
- Check that thread ID is correct
- Verify GitHub API is responding

### "Failed to resolve thread" warning
- Thread may be already resolved
- May need to refresh: `gh api --cache=0`
- Try running again with `--dry-run` first

## Integration with PM Skills

This script complements the PM workflow:

```bash
# 1. Start work on issue
/pm:issue-start 627

# 2. Execute PR Review Response Protocol
# ... extract findings, fix, commit, push ...

# 3. Resolve threads and close issue
.claude/scripts/resolve-pr-threads.sh 627 --replies my-replies.json
/pm:issue-sync 627

# 4. PR is ready for merge
```

---

**Created**: 2026-03-07
**Updated**: 2026-03-07
**Status**: Ready for use
