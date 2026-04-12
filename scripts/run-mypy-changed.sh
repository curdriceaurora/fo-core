#!/usr/bin/env bash
# run-mypy-changed.sh — Run mypy on the files passed as arguments.
# Invoked by the mypy-changed pre-commit hook with pass_filenames: true.
# mypy reads strict settings from pyproject.toml automatically.
set -euo pipefail

if [[ $# -eq 0 ]]; then
    exit 0
fi

if [[ -x .venv/bin/mypy ]]; then
    exec .venv/bin/mypy "$@"
elif command -v mypy &>/dev/null; then
    exec mypy "$@"
else
    echo "error: mypy not found — run 'pip install mypy' or activate your venv" >&2
    exit 1
fi
