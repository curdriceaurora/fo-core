# Triage Policy for Cleanup-Only Test Work

This policy applies to issues whose primary content is: removing dead assertions,
deduplicating overlapping test cases, fixing obsolete mocks, or adding trivial
happy-path coverage with no `src/` delta. It does **not** apply to test work that
accompanies a feature or bug fix — that follows the normal PR process.

## What Qualifies as Cleanup-Only

A test work item is cleanup-only when **all three** of the following are true:

1. No `src/` change (or only comments/docstrings are touched)
2. No new assertion surface — the paths being exercised are already covered by
   equivalent assertions in existing tests
3. Coverage delta is less than 1%

If any condition is false, treat the item as a normal feature or fix.

## Decision Tree

```text
Is the test work already covered by an in-flight PR or issue?
├── Yes → Close as Duplicate: link the in-flight item, label `duplicate`
└── No
    ↓
Is there an open branch/PR touching related src/ code? (fewer than 10 test lines changed)
├── Yes → Bundle: commit on that branch, reference this issue in the PR body
└── No
    ↓
Is the cleanup genuinely valuable (removes confusing tests, improves readability)?
├── No  → Close as Won't Fix: add label `wontfix`, leave a one-line explanation
└── Yes → Standalone PR: branch cleanup/test-<short-slug>, title prefix "test:"
```

## Standalone PR Rules

When the decision is "Standalone PR":

- Branch name: `cleanup/test-<short-slug>`
- PR title must start with `test:`
- PR body must state which metric (if any) improves and by how much
- CI must pass; no new `# noqa` suppressions allowed

## Closing Stale Cleanup Threads

A cleanup issue is stale when it has had **no activity for 60 days** and no open
PR references it. Close with:

1. Add label: `stale`
2. Post this comment:

   > Closing as stale — no activity in 60 days and no in-flight PR.
   > If this is still relevant, please reopen with a PR draft attached.

## What Does Not Belong Here

- Issues touching `src/` beyond comments → normal review process applies
- Coverage ratchet updates (see `scripts/coverage/README.md` at repo root)
- Issues with failing CI → fix the failures first, then triage
