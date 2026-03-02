#!/bin/bash
# File Organizer - macOS Quick Action
# Called by Automator with selected file paths as arguments
#
# Installation: Either install as Automator Quick Action (see README)
# or run install-macos.sh

BACKEND_PORT="${FILE_ORGANIZER_PORT:-8000}"
BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}/api/v1/organize"

organize_path() {
    local path="$1"
    [ -z "$path" ] && return

    # Try REST API if backend is running
    if curl -sf --max-time 5 \
        -X POST "$BACKEND_URL" \
        -H "Content-Type: application/json" \
        -d "{\"paths\": [\"$path\"]}" \
        -o /dev/null 2>/dev/null; then

        osascript -e "display notification \"Organizing: $(basename "$path")\" with title \"File Organizer\""
        return 0
    fi

    # Fall back to CLI
    if command -v file-organizer &>/dev/null; then
        file-organizer organize --input "$path" 2>&1
        osascript -e "display notification \"Done organizing $(basename "$path")\" with title \"File Organizer\""
    else
        osascript -e "display alert \"File Organizer\" message \"Please launch File Organizer app first.\" as warning"
    fi
}

# Handle arguments (from Automator or command line)
if [ $# -gt 0 ]; then
    for path in "$@"; do
        organize_path "$path"
    done
else
    # Read from stdin (Automator passes paths via stdin for file inputs)
    while IFS= read -r path; do
        [ -n "$path" ] && organize_path "$path"
    done
fi
