#!/bin/bash
# Install File Organizer Quick Action for macOS Finder context menu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
set -e

echo "Installing File Organizer macOS Quick Action..."

SERVICES_DIR="$HOME/Library/Services"
WORKFLOW_SRC="$SCRIPT_DIR/macos/OrganizeWithFileOrganizer.workflow"

if [ ! -d "$WORKFLOW_SRC" ]; then
    echo "❌ Workflow not found: $WORKFLOW_SRC"
    exit 1
fi

mkdir -p "$SERVICES_DIR"
cp -r "$WORKFLOW_SRC" "$SERVICES_DIR/"

echo "✅ Quick Action installed to $SERVICES_DIR"
echo ""
echo "To enable: System Settings → Privacy & Security → Extensions → Finder Extensions"
echo "Or: Right-click a file → Quick Actions → 'Organize with File Organizer'"
echo ""
echo "You may need to restart Finder:"
echo "  killall Finder"
