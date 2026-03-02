"""
Tests for desktop system tray configuration.

Validates that tray.rs exists and contains the expected menu items,
and that lib.rs correctly references the tray module.
"""

from pathlib import Path

TAURI_SRC = Path(__file__).parent.parent.parent / "desktop" / "src-tauri" / "src"
TRAY_RS = TAURI_SRC / "tray.rs"
LIB_RS = TAURI_SRC / "lib.rs"


def test_tray_rs_exists():
    """tray.rs must exist in the Tauri src directory."""
    assert TRAY_RS.exists(), f"Expected {TRAY_RS} to exist"


def test_tray_rs_has_show_menu_item():
    """tray.rs must define the 'show' menu item."""
    content = TRAY_RS.read_text()
    assert '"show"' in content, "tray.rs must contain the 'show' menu item id"
    assert "Show Window" in content, "tray.rs must contain the 'Show Window' menu item label"


def test_tray_rs_has_quit_menu_item():
    """tray.rs must define the 'quit' menu item."""
    content = TRAY_RS.read_text()
    assert '"quit"' in content, "tray.rs must contain the 'quit' menu item id"
    assert "Quit" in content, "tray.rs must contain the 'Quit' menu item label"


def test_tray_rs_has_create_tray_function():
    """tray.rs must expose a create_tray public function."""
    content = TRAY_RS.read_text()
    assert "pub fn create_tray" in content, "tray.rs must define a public create_tray function"


def test_tray_rs_uses_tray_icon_builder():
    """tray.rs must use TrayIconBuilder to construct the tray."""
    content = TRAY_RS.read_text()
    assert "TrayIconBuilder" in content, "tray.rs must use TrayIconBuilder"


def test_tray_rs_uses_menu_builder():
    """tray.rs must use MenuBuilder to construct the tray menu."""
    content = TRAY_RS.read_text()
    assert "MenuBuilder" in content, "tray.rs must use MenuBuilder"


def test_lib_rs_declares_tray_module():
    """lib.rs must declare the tray module with 'mod tray;'."""
    content = LIB_RS.read_text()
    assert "mod tray;" in content, "lib.rs must declare 'mod tray;'"


def test_lib_rs_calls_create_tray_in_setup():
    """lib.rs setup() must call tray::create_tray."""
    content = LIB_RS.read_text()
    assert "tray::create_tray" in content, "lib.rs must call tray::create_tray in setup"
