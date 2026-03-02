//! macOS LaunchAgent daemon manager.
//!
//! Manages the file-organizer background daemon using macOS LaunchAgents,
//! which are the standard mechanism for per-user background services on macOS.

use std::fs;
use std::io::{self, ErrorKind};
use std::path::PathBuf;
use std::process::Command;

use super::DaemonManager;

/// Manages the file-organizer daemon as a macOS LaunchAgent.
///
/// LaunchAgent plists are stored at `~/Library/LaunchAgents/{label}.plist`
/// and are loaded/unloaded via `launchctl`.
pub struct MacOsDaemonManager {
    /// The bundle-style label used to identify the LaunchAgent,
    /// e.g. `"com.fileorganizer.daemon"`.
    pub label: String,
}

impl MacOsDaemonManager {
    /// Create a new manager with the given LaunchAgent label.
    pub fn new(label: impl Into<String>) -> Self {
        Self {
            label: label.into(),
        }
    }

    /// Returns the path to the LaunchAgent plist file.
    ///
    /// Resolves to `~/Library/LaunchAgents/{label}.plist`.
    fn plist_path(&self) -> io::Result<PathBuf> {
        let home = dirs_next::home_dir().ok_or_else(|| {
            io::Error::new(ErrorKind::NotFound, "Cannot determine home directory")
        })?;
        Ok(home
            .join("Library")
            .join("LaunchAgents")
            .join(format!("{}.plist", self.label)))
    }

    /// Generate the plist XML for a given binary path.
    ///
    /// The generated plist:
    /// - Sets `Label` to `self.label`
    /// - Sets `ProgramArguments` to `[binary_path]`
    /// - Enables `KeepAlive` so launchd restarts the daemon if it exits
    /// - Enables `RunAtLoad` so the daemon starts immediately when loaded
    /// - Writes stdout/stderr logs to `~/Library/Logs/{label}.{out,err}.log`
    fn generate_plist(&self, binary_path: &PathBuf) -> io::Result<String> {
        let home = dirs_next::home_dir().ok_or_else(|| {
            io::Error::new(ErrorKind::NotFound, "Cannot determine home directory")
        })?;
        let log_dir = home.join("Library").join("Logs");
        let stdout_log = log_dir.join(format!("{}.out.log", self.label));
        let stderr_log = log_dir.join(format!("{}.err.log", self.label));

        let binary_str = binary_path
            .to_str()
            .ok_or_else(|| io::Error::new(ErrorKind::InvalidInput, "Binary path is not valid UTF-8"))?;
        let stdout_str = stdout_log
            .to_str()
            .ok_or_else(|| io::Error::new(ErrorKind::InvalidInput, "Log path is not valid UTF-8"))?;
        let stderr_str = stderr_log
            .to_str()
            .ok_or_else(|| io::Error::new(ErrorKind::InvalidInput, "Log path is not valid UTF-8"))?;

        Ok(format!(
            r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{binary}</string>
    </array>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{stdout}</string>
    <key>StandardErrorPath</key>
    <string>{stderr}</string>
</dict>
</plist>
"#,
            label = self.label,
            binary = binary_str,
            stdout = stdout_str,
            stderr = stderr_str,
        ))
    }

    /// Run a `launchctl` command and return an `io::Result`.
    fn launchctl(&self, args: &[&str]) -> io::Result<()> {
        let status = Command::new("launchctl").args(args).status()?;
        if status.success() {
            Ok(())
        } else {
            Err(io::Error::new(
                ErrorKind::Other,
                format!(
                    "launchctl {} exited with status {}",
                    args.join(" "),
                    status
                ),
            ))
        }
    }
}

impl DaemonManager for MacOsDaemonManager {
    fn install(&self, binary_path: &PathBuf) -> io::Result<()> {
        let plist_path = self.plist_path()?;

        // Ensure the LaunchAgents directory exists.
        if let Some(parent) = plist_path.parent() {
            fs::create_dir_all(parent)?;
        }

        let plist_content = self.generate_plist(binary_path)?;
        fs::write(&plist_path, plist_content)?;
        Ok(())
    }

    fn uninstall(&self) -> io::Result<()> {
        // Stop the daemon first; ignore errors if it is not running.
        let _ = self.stop();

        let plist_path = self.plist_path()?;
        if plist_path.exists() {
            fs::remove_file(&plist_path)?;
        }
        Ok(())
    }

    fn start(&self) -> io::Result<()> {
        let plist_path = self.plist_path()?;
        let plist_str = plist_path
            .to_str()
            .ok_or_else(|| io::Error::new(ErrorKind::InvalidInput, "Plist path is not valid UTF-8"))?;
        self.launchctl(&["load", plist_str])
    }

    fn stop(&self) -> io::Result<()> {
        let plist_path = self.plist_path()?;
        let plist_str = plist_path
            .to_str()
            .ok_or_else(|| io::Error::new(ErrorKind::InvalidInput, "Plist path is not valid UTF-8"))?;
        self.launchctl(&["unload", plist_str])
    }

    fn is_running(&self) -> bool {
        Command::new("launchctl")
            .args(["list", &self.label])
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false)
    }

    fn enable_autostart(&self) -> io::Result<()> {
        // On macOS, RunAtLoad=true in the plist handles autostart.
        // Re-loading the plist applies any updated settings.
        let plist_path = self.plist_path()?;
        if !plist_path.exists() {
            return Err(io::Error::new(
                ErrorKind::NotFound,
                "Daemon is not installed; call install() first",
            ));
        }
        let plist_str = plist_path
            .to_str()
            .ok_or_else(|| io::Error::new(ErrorKind::InvalidInput, "Plist path is not valid UTF-8"))?;
        // Unload then load with -w to persistently enable RunAtLoad=true.
        let _ = self.launchctl(&["unload", "-w", plist_str]);
        self.launchctl(&["load", "-w", plist_str])
    }

    fn disable_autostart(&self) -> io::Result<()> {
        // Use -w flag so the disable persists across reboots (writes Disabled=true
        // to the launchd override database, not just the current session).
        let plist_path = self.plist_path()?;
        if !plist_path.exists() {
            return Ok(()); // Nothing to disable.
        }
        let plist_str = plist_path
            .to_str()
            .ok_or_else(|| io::Error::new(ErrorKind::InvalidInput, "Plist path is not valid UTF-8"))?;
        self.launchctl(&["unload", "-w", plist_str])
    }
}

// ---------------------------------------------------------------------------
// Unit tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn make_manager() -> MacOsDaemonManager {
        MacOsDaemonManager::new("com.fileorganizer.daemon")
    }

    /// Verify that the generated plist XML contains all required keys.
    #[test]
    fn test_plist_generation() {
        let manager = make_manager();
        let binary = PathBuf::from("/usr/local/bin/file-organizer-daemon");
        let plist = manager
            .generate_plist(&binary)
            .expect("plist generation should succeed");

        assert!(plist.contains("<key>Label</key>"), "Missing Label key");
        assert!(
            plist.contains("com.fileorganizer.daemon"),
            "Label value not present"
        );
        assert!(
            plist.contains("<key>ProgramArguments</key>"),
            "Missing ProgramArguments key"
        );
        assert!(
            plist.contains("/usr/local/bin/file-organizer-daemon"),
            "Binary path not in plist"
        );
        assert!(
            plist.contains("<key>KeepAlive</key>"),
            "Missing KeepAlive key"
        );
        assert!(plist.contains("<true/>"), "KeepAlive not set to true");
        assert!(
            plist.contains("<key>RunAtLoad</key>"),
            "Missing RunAtLoad key"
        );
        assert!(
            plist.contains("<key>StandardOutPath</key>"),
            "Missing StandardOutPath key"
        );
        assert!(
            plist.contains("<key>StandardErrorPath</key>"),
            "Missing StandardErrorPath key"
        );
    }

    /// Verify the install path is computed correctly.
    #[test]
    fn test_install_path() {
        let manager = make_manager();
        let path = manager.plist_path().expect("plist_path should succeed");

        // Must end with the label + .plist
        assert!(
            path.file_name()
                .and_then(|n| n.to_str())
                .map(|n| n == "com.fileorganizer.daemon.plist")
                .unwrap_or(false),
            "Unexpected filename: {:?}",
            path.file_name()
        );

        // Must be inside ~/Library/LaunchAgents
        assert!(
            path.to_str()
                .map(|s| s.contains("Library/LaunchAgents"))
                .unwrap_or(false),
            "Plist not inside LaunchAgents: {:?}",
            path
        );
    }

    /// Verify that a label is constructed correctly from a bundle ID.
    #[test]
    fn test_label_from_bundle_id() {
        let bundle_id = "com.fileorganizer.daemon";
        let manager = MacOsDaemonManager::new(bundle_id);
        assert_eq!(manager.label, "com.fileorganizer.daemon");

        // A different bundle ID is preserved verbatim.
        let manager2 = MacOsDaemonManager::new("io.example.myapp");
        assert_eq!(manager2.label, "io.example.myapp");
    }
}
