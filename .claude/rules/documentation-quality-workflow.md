# Documentation Quality Workflow

**Goal**: Zero documentation errors in code review.

## The Rule: Source First, Always

Never write a documentation claim without first verifying it in actual source code.
This prevents 90% of errors before they're generated.

---

## Per-Document Workflow

```
1. DISCOVER  →  read pyproject.toml, ci.yml, src/ BEFORE writing anything
2. WRITE     →  source-first; copy examples from test files, not memory
3. REVIEW    →  contradiction check + section range check
4. VALIDATE  →  bash .claude/scripts/pre-commit-validation.sh (must pass)
5. COMMIT    →  git add <doc-files> && git commit -m "docs: ..."
```

### Step 1: Discover Sources

| Claim type | Where to verify |
|-----------|----------------|
| Coverage gates | `pyproject.toml` + `.github/workflows/ci.yml` (multiple gates — see `ci-generation-patterns.md` C4) |
| CI behavior | `.github/workflows/ci.yml` — read the actual job steps |
| Method/class exists | `rg "def method_name\|class ClassName" src/` |
| Feature exists | `ls src/<module>/` or grep test imports |
| Config keys | `src/config/schema.py` — `AppConfig` Pydantic model |

### Step 2: Write

- Read actual code first; extract real values; don't paraphrase
- Copy code examples from actual test files (include file + line reference)
- Document only what exists; no assumptions

### Step 3: Review

Before committing, answer:
- Does any section contradict another? (feature "complete" vs "0% coverage")
- Are section range entries correct? (e.g. "Medium 70–89%" contains only 70–89% values)
- Do all code examples reference methods that actually exist?

### Step 4: Validate

```bash
bash .claude/scripts/pre-commit-validation.sh
# Must show: ✓ Documentation content verification passed
```

---

## Common Errors and Prevention

| Error | Prevention |
|-------|-----------|
| Wrong coverage % | Check `ci-generation-patterns.md` C4 gate table before writing any number |
| Non-existent method | `rg "def method_name" src/` before writing |
| Stale `parsers` extra | Removed — valid extras: `dev cloud llama mlx claude audio video dedup archive scientific cad build search all` |
| Stale `plugins` reference | `plugins` package removed; no plugin system exists |
| Stale API/web routes | fo-core is CLI-only; no FastAPI, no `@router` handlers |
| `ollama ls` command | Correct command is `ollama list` |

---

## References

- `documentation-generation-checklist.md` — full pre-writing discovery phases
- `documentation-verification.md` — automated check descriptions and manual checklist
- `ci-generation-patterns.md` (C4) — authoritative coverage gate table

**Last Updated**: 2026-04-10
