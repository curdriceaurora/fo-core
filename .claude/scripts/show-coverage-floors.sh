#!/usr/bin/env bash
# show-coverage-floors.sh — Display per-module coverage floor status.
#
# Usage:
#   bash .claude/scripts/show-coverage-floors.sh [PATTERN]
#   bash .claude/scripts/show-coverage-floors.sh cli
#   bash .claude/scripts/show-coverage-floors.sh pipeline
#   bash .claude/scripts/show-coverage-floors.sh "cli|pipeline|parallel|optimization"
#
# Without a pattern, shows all modules below 80%.
# With a pattern (grep -E), filters to matching module paths.
# Outputs: ✓ (≥80%) or ✗ (<80%) marker, module path, floor %.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
BASELINE="$REPO_ROOT/scripts/coverage/integration_module_floor_baseline.json"
PATTERN="${1:-}"

python3 - "$BASELINE" "$PATTERN" <<'PYEOF'
import json, sys

baseline_path, pattern = sys.argv[1], sys.argv[2]
with open(baseline_path) as f:
    data = json.load(f)

mods = data.get("modules", {})
items = sorted(mods.items(), key=lambda x: x[1])

for path, floor in items:
    short = path.replace("src/", "")
    if pattern and not __import__("re").search(pattern, path):
        continue
    if not pattern and floor >= 80:
        continue
    mark = "✓" if floor >= 80 else "✗"
    print(f"{mark}  {short:<65} {floor:5.1f}%")
PYEOF
