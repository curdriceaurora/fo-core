# Coverage gates

fo-core enforces five independent coverage gates. A change to any one requires
a matching change to this doc and — where relevant — to the
`.claude/rules/ci-generation-patterns.md` C4 table.

## The ladder

| Gate | Threshold | Where declared | When it runs |
|------|-----------|---------------|--------------|
| Unit test floor | **95%** line | `pyproject.toml` `[tool.pytest.ini_options]` `addopts` `--cov-fail-under=95` | Local `pytest` + every CI job that runs the unit suite |
| PR diff coverage | **80%** line | `.github/workflows/ci.yml` `diff-cover ... --fail-under=80` step | PR only — scoped to changed lines |
| Main-push floor | **93%** line | `.github/workflows/ci.yml` `coverage report --fail-under=93` step | Push to `main` |
| Docstring coverage | **95%** | `.github/workflows/ci.yml` `interrogate -v src/ --fail-under 95` step | Push to `main` |
| Integration floor | **71.9%** line + branch | `.github/workflows/ci.yml` `coverage report --fail-under=71.9` step (blocking — see note below) | Push to `main` (integration job) |

> **Note on the integration floor's two-step pattern.** The raw
> `coverage report --fail-under=71.9` step carries `continue-on-error: true`
> so a failure there doesn't abort the job mid-run. A subsequent
> `Enforce integration gate outcomes` step inspects
> `steps.global_gate.outcome` and exits 1 if it isn't `success` — that is
> the gate's actual failure point. The two-step shape exists so the
> integration-tests outcome + per-module gate + global floor can each be
> reported independently before the job fails.

## Which number should I quote?

- Local `pytest` runs → **95%** (unit floor)
- PR CI → **80%** (diff coverage on changed lines)
- Overall project health on `main` → **93%** (full-suite floor)
- Docstring coverage → **95%** (interrogate, push-only)
- Integration subset → **71.9%** (line + branch, push-only)

## Changing a gate

Because the gates are declared in two separate places (`pyproject.toml`
and the workflow files) and referenced from multiple rule files, a bump
requires a sweep:

1. Change the numeric threshold in the canonical declaration (table above).
2. Update every doc reference — run `rg <old>` across `docs/`, `.github/`,
   `README.md`, `CONTRIBUTING.md`, `.claude/rules/` and patch each hit.
3. If raising the floor, run the matching measurement first and attach
   the result to the PR description so reviewers can reproduce.
   For integration, use `bash .claude/scripts/measure-integration-coverage.sh`
   (see `ci-generation-patterns.md` rule C7).

## See also

- `.claude/rules/ci-generation-patterns.md` — rule C4 (coverage-gate stale references).
- `.claude/rules/documentation-verification.md` — doc verification for gate claims.
