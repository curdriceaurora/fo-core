#!/bin/bash
# Install File Organizer context menus for Linux file managers

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
set -e

echo "Installing File Organizer context menus..."

# Install Nautilus script
NAUTILUS_DIR="$HOME/.local/share/nautilus/scripts"
if command -v nautilus &>/dev/null || [ -d "$NAUTILUS_DIR" ]; then
    mkdir -p "$NAUTILUS_DIR"
    cp "$SCRIPT_DIR/nautilus/Organize with File Organizer" "$NAUTILUS_DIR/"
    chmod +x "$NAUTILUS_DIR/Organize with File Organizer"
    echo "✅ Nautilus script installed to $NAUTILUS_DIR"
else
    echo "⚠️  Nautilus not found, skipping Nautilus integration"
fi

# Install Dolphin service menu (KDE 5 / kservices5)
KDE5_DIR="$HOME/.local/share/kservices5/ServiceMenus"
if command -v dolphin &>/dev/null || [ -d "$(dirname $KDE5_DIR)" ]; then
    mkdir -p "$KDE5_DIR"
    cp "$SCRIPT_DIR/dolphin/file-organizer.desktop" "$KDE5_DIR/"
    echo "✅ Dolphin service menu installed to $KDE5_DIR"
else
    echo "⚠️  Dolphin not found, skipping Dolphin integration"
fi

# Install for KDE 6 (kf6)
KDE6_DIR="$HOME/.local/share/kf6/servicemenus"
if [ -d "$(dirname $(dirname $KDE6_DIR))" ]; then
    mkdir -p "$KDE6_DIR"
    cp "$SCRIPT_DIR/dolphin/file-organizer.desktop" "$KDE6_DIR/"
    echo "✅ KDE 6 service menu installed to $KDE6_DIR"
fi

echo ""
echo "Context menus installed successfully!"
echo "Restart your file manager to see the changes."
