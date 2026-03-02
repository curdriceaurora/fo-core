pub mod macos;
pub mod linux;
pub mod windows;

use std::path::Path;

/// Common interface for platform-specific daemon management.
pub trait DaemonManager {
    /// Install daemon configuration (LaunchAgent plist, systemd unit, etc.)
    fn install(&self, binary_path: &Path) -> std::io::Result<()>;

    /// Uninstall daemon configuration
    fn uninstall(&self) -> std::io::Result<()>;

    /// Start the daemon
    fn start(&self) -> std::io::Result<()>;

    /// Stop the daemon
    fn stop(&self) -> std::io::Result<()>;

    /// Returns true if daemon is currently running
    fn is_running(&self) -> bool;

    /// Enable auto-launch on login
    fn enable_autostart(&self) -> std::io::Result<()>;

    /// Disable auto-launch on login
    fn disable_autostart(&self) -> std::io::Result<()>;
}
