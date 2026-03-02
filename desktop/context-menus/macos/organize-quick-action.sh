#!/bin/bash
# File Organizer - macOS Quick Action
# Called by Automator with selected file paths as arguments
#
# Installation: Either install as Automator Quick Action (see README)
# or run install-macos.sh

# Port discovery: use FILE_ORGANIZER_PORT env var if set, otherwise
# fall back to 8000 (the default when the sidecar port file is unavailable).
BACKEND_PORT="${FILE_ORGANIZER_PORT:-8000}"
BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}/api/v1/organize"

organize_path() {
    local path="$1"
    [ -z "$path" ] && return

    # Safely construct JSON payload (escape backslashes, quotes, and control chars)
    local escaped_path
    escaped_path=$(printf '%s' "$path" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\t/\\t/g' -e 's/\n/\\n/g')
    local json_payload="{\"paths\": [\"${escaped_path}\"]}"

    # Try REST API if backend is running
    if curl -sf --max-time 5 \
        -X POST "$BACKEND_URL" \
        -H "Content-Type: application/json" \
        -d "$json_payload" \
        -o /dev/null 2>/dev/null; then

        osascript -e "display notification \"Organizing: $(basename "$path")\" with title \"File Organizer\""
        return 0
    fi

    # Fall back to CLI
    if command -v file-organizer &>/dev/null; then
        if file-organizer organize --input "$path" 2>&1; then
            osascript -e "display notification \"Done organizing $(basename "$path")\" with title \"File Organizer\""
        else
            osascript -e "display alert \"File Organizer\" message \"Failed to organize $(basename "$path"). Check logs for details.\" as warning"
        fi
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
