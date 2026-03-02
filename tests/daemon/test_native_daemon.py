"""
Tests for native platform daemon manager implementations.

Validates that Rust source files for each platform daemon manager
exist, contain the correct trait implementations, and include
appropriate unit tests.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci

REPO_ROOT = Path(__file__).resolve().parents[2]
DAEMON_DIR = REPO_ROOT / "desktop" / "src-tauri" / "src" / "daemon"


class TestLinuxDaemonManager(unittest.TestCase):
    """Validates the Linux systemd daemon module (linux.rs)."""

    def setUp(self) -> None:
        self.daemon_dir = DAEMON_DIR
        self.linux_rs = self.daemon_dir / "linux.rs"

    def test_linux_rs_exists(self) -> None:
        """linux.rs must exist in the daemon directory."""
        self.assertTrue(
            self.linux_rs.exists(),
            f"linux.rs not found at {self.linux_rs}",
        )

    def test_implements_daemon_manager(self) -> None:
        """linux.rs must implement the DaemonManager trait."""
        content = self.linux_rs.read_text()
        self.assertIn(
            "impl DaemonManager for",
            content,
            "Expected 'impl DaemonManager for' in linux.rs",
        )

    def test_unit_file_sections(self) -> None:
        """Unit file template must include all three systemd sections."""
        content = self.linux_rs.read_text()
        for section in ["[Unit]", "[Service]", "[Install]"]:
            self.assertIn(
                section,
                content,
                f"Expected systemd section '{section}' in linux.rs",
            )

    def test_systemctl_commands(self) -> None:
        """linux.rs must use systemctl with --user flag."""
        content = self.linux_rs.read_text()
        self.assertIn("systemctl", content, "Expected 'systemctl' in linux.rs")
        self.assertIn("--user", content, "Expected '--user' flag in linux.rs")

    def test_restart_policy(self) -> None:
        """Unit file must specify Restart=on-failure."""
        content = self.linux_rs.read_text()
        self.assertIn(
            "Restart=on-failure",
            content,
            "Expected 'Restart=on-failure' in linux.rs",
        )

    def test_rust_tests_present(self) -> None:
        """linux.rs must include Rust unit tests."""
        content = self.linux_rs.read_text()
        self.assertIn(
            "#[cfg(test)]",
            content,
            "Expected '#[cfg(test)]' module in linux.rs",
        )
        self.assertIn(
            "#[test]",
            content,
            "Expected '#[test]' attributes in linux.rs",
        )

    def test_struct_defined(self) -> None:
        """LinuxDaemonManager struct must be defined."""
        content = self.linux_rs.read_text()
        self.assertIn(
            "struct LinuxDaemonManager",
            content,
            "Expected 'struct LinuxDaemonManager' in linux.rs",
        )

    def test_service_name_field(self) -> None:
        """LinuxDaemonManager must have a service_name field."""
        content = self.linux_rs.read_text()
        self.assertIn(
            "service_name",
            content,
            "Expected 'service_name' field in linux.rs",
        )

    def test_systemd_user_path(self) -> None:
        """Unit file path must point to ~/.config/systemd/user/."""
        content = self.linux_rs.read_text()
        self.assertIn(
            ".config/systemd/user",
            content,
            "Expected '.config/systemd/user' path in linux.rs",
        )

    def test_service_extension(self) -> None:
        """Unit file must use the .service extension."""
        content = self.linux_rs.read_text()
        self.assertIn(
            ".service",
            content,
            "Expected '.service' extension in linux.rs",
        )

    def test_generate_unit_file_method(self) -> None:
        """generate_unit_file helper method must be present."""
        content = self.linux_rs.read_text()
        self.assertIn(
            "generate_unit_file",
            content,
            "Expected 'generate_unit_file' method in linux.rs",
        )

    def test_unit_file_path_method(self) -> None:
        """unit_file_path helper method must be present."""
        content = self.linux_rs.read_text()
        self.assertIn(
            "unit_file_path",
            content,
            "Expected 'unit_file_path' method in linux.rs",
        )

    def test_mod_rs_includes_linux(self) -> None:
        """mod.rs must declare the linux module."""
        mod_rs = self.daemon_dir / "mod.rs"
        self.assertTrue(mod_rs.exists(), "mod.rs not found")
        content = mod_rs.read_text()
        self.assertIn(
            "pub mod linux",
            content,
            "Expected 'pub mod linux' in mod.rs",
        )

    def test_restart_sec_present(self) -> None:
        """Unit file must include RestartSec directive."""
        content = self.linux_rs.read_text()
        self.assertIn(
            "RestartSec=",
            content,
            "Expected 'RestartSec=' in linux.rs",
        )

    def test_wanted_by_default_target(self) -> None:
        """Unit file [Install] section must use WantedBy=default.target."""
        content = self.linux_rs.read_text()
        self.assertIn(
            "WantedBy=default.target",
            content,
            "Expected 'WantedBy=default.target' in linux.rs",
        )


class TestMacOsDaemonManager(unittest.TestCase):
    """Python integration tests for macOS LaunchAgent daemon manager."""

    def test_macos_rs_file_exists(self) -> None:
        """macos.rs source file must exist."""
        macos_rs = DAEMON_DIR / "macos.rs"
        self.assertTrue(macos_rs.exists(), f"macos.rs not found at {macos_rs}")

    def test_macos_implements_daemon_manager_trait(self) -> None:
        """MacOsDaemonManager must implement DaemonManager trait."""
        content = (DAEMON_DIR / "macos.rs").read_text()
        self.assertIn("impl DaemonManager for MacOsDaemonManager", content)

    def test_plist_contains_label_key(self) -> None:
        """Plist template must include the Label key."""
        content = (DAEMON_DIR / "macos.rs").read_text()
        self.assertIn("Label", content)

    def test_plist_contains_program_arguments_key(self) -> None:
        """Plist template must include the ProgramArguments key."""
        content = (DAEMON_DIR / "macos.rs").read_text()
        self.assertIn("ProgramArguments", content)

    def test_plist_contains_keep_alive_key(self) -> None:
        """Plist template must include the KeepAlive key."""
        content = (DAEMON_DIR / "macos.rs").read_text()
        self.assertIn("KeepAlive", content)

    def test_plist_contains_run_at_load_key(self) -> None:
        """Plist template must include the RunAtLoad key."""
        content = (DAEMON_DIR / "macos.rs").read_text()
        self.assertIn("RunAtLoad", content)

    def test_uses_launchctl(self) -> None:
        """macos.rs must invoke launchctl for daemon management."""
        content = (DAEMON_DIR / "macos.rs").read_text()
        self.assertIn("launchctl", content)

    def test_installs_to_library_launchagents(self) -> None:
        """Plist must be installed under ~/Library/LaunchAgents."""
        content = (DAEMON_DIR / "macos.rs").read_text()
        self.assertIn("Library/LaunchAgents", content)

    def test_has_rust_tests(self) -> None:
        """macos.rs must include Rust unit tests."""
        content = (DAEMON_DIR / "macos.rs").read_text()
        self.assertIn("#[cfg(test)]", content)
        self.assertIn("#[test]", content)

    def test_disable_autostart_uses_persistent_flag(self) -> None:
        """disable_autostart must use -w flag for persistent disable."""
        content = (DAEMON_DIR / "macos.rs").read_text()
        self.assertIn('"-w"', content, "disable_autostart should use launchctl -w flag")


class TestWindowsDaemonManager(unittest.TestCase):
    """Validates Windows Scheduled Task daemon module (windows.rs)."""

    def setUp(self) -> None:
        self.daemon_dir = DAEMON_DIR
        self.windows_rs = self.daemon_dir / "windows.rs"

    def test_windows_rs_exists(self) -> None:
        """windows.rs must exist in the daemon directory."""
        self.assertTrue(
            self.windows_rs.exists(),
            f"windows.rs not found at {self.windows_rs}",
        )

    def test_implements_daemon_manager(self) -> None:
        """windows.rs must implement the DaemonManager trait."""
        content = self.windows_rs.read_text()
        self.assertIn(
            "impl DaemonManager for",
            content,
            "Expected 'impl DaemonManager for' in windows.rs",
        )

    def test_schtasks_commands(self) -> None:
        """windows.rs must use schtasks.exe for task management."""
        content = self.windows_rs.read_text()
        self.assertIn(
            "schtasks",
            content,
            "Expected 'schtasks' command in windows.rs",
        )

    def test_registry_key_present(self) -> None:
        """windows.rs must reference the Windows Registry CurrentVersion Run key."""
        content = self.windows_rs.read_text()
        self.assertIn(
            r"CurrentVersion\Run",
            content,
            r"Expected 'CurrentVersion\Run' registry path in windows.rs",
        )

    def test_start_stop_methods(self) -> None:
        """windows.rs must define both fn start and fn stop."""
        content = self.windows_rs.read_text()
        self.assertIn(
            "fn start",
            content,
            "Expected 'fn start' in windows.rs",
        )
        self.assertIn(
            "fn stop",
            content,
            "Expected 'fn stop' in windows.rs",
        )

    def test_rust_tests_present(self) -> None:
        """windows.rs must include Rust unit tests."""
        content = self.windows_rs.read_text()
        self.assertIn(
            "#[cfg(test)]",
            content,
            "Expected '#[cfg(test)]' module in windows.rs",
        )
        self.assertIn(
            "#[test]",
            content,
            "Expected '#[test]' attributes in windows.rs",
        )

    def test_struct_defined(self) -> None:
        """WindowsDaemonManager struct must be defined."""
        content = self.windows_rs.read_text()
        self.assertIn(
            "struct WindowsDaemonManager",
            content,
            "Expected 'struct WindowsDaemonManager' in windows.rs",
        )

    def test_task_name_field(self) -> None:
        """WindowsDaemonManager must have a task_name field."""
        content = self.windows_rs.read_text()
        self.assertIn(
            "task_name",
            content,
            "Expected 'task_name' field in windows.rs",
        )

    def test_hkcu_registry_path(self) -> None:
        """Autostart registry key must be under HKCU."""
        content = self.windows_rs.read_text()
        self.assertIn(
            "HKCU",
            content,
            "Expected 'HKCU' registry hive in windows.rs",
        )

    def test_is_running_method(self) -> None:
        """windows.rs must implement fn is_running."""
        content = self.windows_rs.read_text()
        self.assertIn(
            "fn is_running",
            content,
            "Expected 'fn is_running' in windows.rs",
        )

    def test_install_uninstall_methods(self) -> None:
        """windows.rs must implement both fn install and fn uninstall."""
        content = self.windows_rs.read_text()
        self.assertIn(
            "fn install",
            content,
            "Expected 'fn install' in windows.rs",
        )
        self.assertIn(
            "fn uninstall",
            content,
            "Expected 'fn uninstall' in windows.rs",
        )

    def test_enable_disable_autostart(self) -> None:
        """windows.rs must implement enable_autostart and disable_autostart."""
        content = self.windows_rs.read_text()
        self.assertIn(
            "fn enable_autostart",
            content,
            "Expected 'fn enable_autostart' in windows.rs",
        )
        self.assertIn(
            "fn disable_autostart",
            content,
            "Expected 'fn disable_autostart' in windows.rs",
        )

    def test_build_create_command_method(self) -> None:
        """build_create_command helper method must be present."""
        content = self.windows_rs.read_text()
        self.assertIn(
            "build_create_command",
            content,
            "Expected 'build_create_command' method in windows.rs",
        )

    def test_autostart_registry_key_method(self) -> None:
        """autostart_registry_key static method must be present."""
        content = self.windows_rs.read_text()
        self.assertIn(
            "autostart_registry_key",
            content,
            "Expected 'autostart_registry_key' method in windows.rs",
        )

    def test_onlogon_trigger(self) -> None:
        """Scheduled task must use onlogon trigger for auto-start."""
        content = self.windows_rs.read_text()
        self.assertIn(
            "onlogon",
            content,
            "Expected 'onlogon' trigger in windows.rs",
        )

    def test_mod_rs_includes_windows(self) -> None:
        """mod.rs must declare the windows module."""
        mod_rs = self.daemon_dir / "mod.rs"
        self.assertTrue(mod_rs.exists(), "mod.rs not found")
        content = mod_rs.read_text()
        self.assertIn(
            "pub mod windows",
            content,
            "Expected 'pub mod windows' in mod.rs",
        )

    def test_reg_exe_used_for_registry(self) -> None:
        """windows.rs must use reg.exe for registry operations."""
        content = self.windows_rs.read_text()
        self.assertIn(
            '"reg"',
            content,
            "Expected reg.exe ('\"reg\"') usage in windows.rs",
        )

    def test_schtasks_query_for_is_running(self) -> None:
        """is_running must use schtasks /query to check task status."""
        content = self.windows_rs.read_text()
        self.assertIn(
            "/query",
            content,
            "Expected '/query' subcommand for schtasks in windows.rs",
        )


if __name__ == "__main__":
    unittest.main()
