#!/usr/bin/env bash
# run-mypy-changed.sh — Run mypy on the files passed as arguments.
# Invoked by the mypy-changed pre-commit hook with pass_filenames: true.
# mypy reads strict settings from pyproject.toml automatically.
set -euo pipefail

if [[ $# -eq 0 ]]; then
    exit 0
fi

exec mypy "$@"
