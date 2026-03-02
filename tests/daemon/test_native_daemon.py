"""
Tests for native platform daemon manager implementations.

Validates that Rust source files for each platform daemon manager
exist, contain the correct trait implementations, and include
appropriate unit tests.
"""

from __future__ import annotations

import unittest
from pathlib import Path


DAEMON_DIR = Path("desktop/src-tauri/src/daemon")


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


if __name__ == "__main__":
    unittest.main()
