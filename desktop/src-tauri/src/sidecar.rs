use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const MAX_RETRIES: u32 = 3;
const HEALTH_POLL_TIMEOUT_SECS: u64 = 30;
const SHUTDOWN_WAIT_SECS: u64 = 5;

#[derive(Debug, Clone, PartialEq)]
pub enum SidecarState {
    Stopped,
    Starting,
    Ready,
    Crashed,
}

pub struct SidecarManager {
    binary_path: PathBuf,
    port: u16,
    state: Arc<Mutex<SidecarState>>,
    child: Arc<Mutex<Option<Child>>>,
    retry_count: Arc<Mutex<u32>>,
}

impl SidecarManager {
    pub fn new(binary_path: PathBuf) -> std::io::Result<Self> {
        let port = Self::find_available_port()?;
        Ok(Self {
            binary_path,
            port,
            state: Arc::new(Mutex::new(SidecarState::Stopped)),
            child: Arc::new(Mutex::new(None)),
            retry_count: Arc::new(Mutex::new(0)),
        })
    }

    /// Bind to port 0 and read the OS-assigned port
    fn find_available_port() -> std::io::Result<u16> {
        let listener = TcpListener::bind("127.0.0.1:0")?;
        Ok(listener.local_addr()?.port())
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    pub fn health_url(&self) -> String {
        format!("http://127.0.0.1:{}/api/v1/health", self.port)
    }

    /// Spawn the sidecar binary with `--port <port>` argument
    pub fn start(&self) -> std::io::Result<()> {
        *self.state.lock().unwrap() = SidecarState::Starting;

        let child = Command::new(&self.binary_path)
            .arg("--port")
            .arg(self.port.to_string())
            .spawn()?;

        *self.child.lock().unwrap() = Some(child);
        Ok(())
    }

    /// Poll the health endpoint with exponential backoff until ready or timeout.
    pub fn wait_until_ready(&self) -> Result<(), String> {
        let timeout = Duration::from_secs(HEALTH_POLL_TIMEOUT_SECS);
        let start = Instant::now();
        let mut delay_ms = 100u64;

        loop {
            if start.elapsed() > timeout {
                *self.state.lock().unwrap() = SidecarState::Crashed;
                return Err(format!(
                    "Backend did not become ready within {}s",
                    HEALTH_POLL_TIMEOUT_SECS
                ));
            }

            if self.check_health() {
                *self.state.lock().unwrap() = SidecarState::Ready;
                return Ok(());
            }

            std::thread::sleep(Duration::from_millis(delay_ms));
            delay_ms = (delay_ms * 2).min(5000); // cap at 5 seconds
        }
    }

    /// Perform a minimal HTTP GET to the health endpoint; returns true on HTTP 200.
    fn check_health(&self) -> bool {
        use std::io::{Read, Write};
        use std::net::{SocketAddr, TcpStream};

        let addr = format!("127.0.0.1:{}", self.port);
        let sock_addr: SocketAddr = match addr.parse() {
            Ok(a) => a,
            Err(_) => return false,
        };

        if let Ok(mut stream) = TcpStream::connect_timeout(&sock_addr, Duration::from_millis(500)) {
            let request = format!(
                "GET /api/v1/health HTTP/1.0\r\nHost: 127.0.0.1:{}\r\n\r\n",
                self.port
            );
            if stream.write_all(request.as_bytes()).is_ok() {
                let mut response = String::new();
                let _ = stream.read_to_string(&mut response);
                return response.starts_with("HTTP/1.0 200")
                    || response.starts_with("HTTP/1.1 200");
            }
        }
        false
    }

    /// Check whether the child process is still alive.
    /// If it has exited, attempt to restart it up to MAX_RETRIES times.
    /// Returns `true` if the sidecar is running (or successfully restarted),
    /// `false` if the maximum retry count has been exceeded.
    pub fn monitor(&self) -> bool {
        // First check: is the process still running?
        let process_exited = {
            let mut child_guard = self.child.lock().unwrap();
            match child_guard.as_mut() {
                None => return false,
                Some(child) => match child.try_wait() {
                    Ok(Some(_)) => true,     // Process has exited
                    Ok(None) => return true, // Still running
                    Err(_) => return false,
                },
            }
        };

        if !process_exited {
            return true;
        }

        // Process exited — decide whether to restart
        let should_restart = {
            let mut retries = self.retry_count.lock().unwrap();
            if *retries < MAX_RETRIES {
                *retries += 1;
                true
            } else {
                false
            }
        };

        if should_restart {
            *self.state.lock().unwrap() = SidecarState::Starting;
            let _ = self.start();
            true
        } else {
            *self.state.lock().unwrap() = SidecarState::Crashed;
            false
        }
    }

    /// Gracefully shut down the sidecar.
    ///
    /// On Unix, sends SIGTERM via the system `kill` command and waits up to
    /// SHUTDOWN_WAIT_SECS seconds for the process to exit before force-killing.
    /// On Windows, immediately calls `kill()` (no SIGTERM equivalent).
    pub fn shutdown(&self) {
        let mut child_guard = self.child.lock().unwrap();
        if let Some(child) = child_guard.as_mut() {
            // Attempt graceful termination
            #[cfg(unix)]
            {
                let pid = child.id();
                let _ = Command::new("kill")
                    .arg("-TERM")
                    .arg(pid.to_string())
                    .status();
            }

            // On Windows there is no SIGTERM; fall straight to kill() after the wait.
            // Wait up to SHUTDOWN_WAIT_SECS for the process to exit gracefully.
            let deadline = Instant::now() + Duration::from_secs(SHUTDOWN_WAIT_SECS);
            loop {
                if Instant::now() > deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    break;
                }
                match child.try_wait() {
                    Ok(Some(_)) => break,
                    _ => std::thread::sleep(Duration::from_millis(100)),
                }
            }
        }
        *self.state.lock().unwrap() = SidecarState::Stopped;
    }

    pub fn state(&self) -> SidecarState {
        self.state.lock().unwrap().clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_find_available_port() {
        let port = SidecarManager::find_available_port().unwrap();
        assert!(port > 1024, "Port should be > 1024, got {}", port);
        assert!(port < 65535, "Port should be < 65535, got {}", port);
    }

    #[test]
    fn test_health_url_format() {
        let mgr = SidecarManager {
            binary_path: PathBuf::from("/usr/bin/file-organizer"),
            port: 12345,
            state: Arc::new(Mutex::new(SidecarState::Stopped)),
            child: Arc::new(Mutex::new(None)),
            retry_count: Arc::new(Mutex::new(0)),
        };
        assert_eq!(mgr.health_url(), "http://127.0.0.1:12345/api/v1/health");
    }

    #[test]
    fn test_initial_state_is_stopped() {
        let mgr = SidecarManager {
            binary_path: PathBuf::from("/usr/bin/file-organizer"),
            port: 12346,
            state: Arc::new(Mutex::new(SidecarState::Stopped)),
            child: Arc::new(Mutex::new(None)),
            retry_count: Arc::new(Mutex::new(0)),
        };
        assert_eq!(mgr.state(), SidecarState::Stopped);
    }

    #[test]
    fn test_max_retries_constant() {
        assert_eq!(MAX_RETRIES, 3);
    }

    #[test]
    fn test_shutdown_wait_constant() {
        assert_eq!(SHUTDOWN_WAIT_SECS, 5);
    }

    #[test]
    fn test_health_poll_timeout_constant() {
        assert_eq!(HEALTH_POLL_TIMEOUT_SECS, 30);
    }

    #[test]
    fn test_port_is_set_on_new() {
        let port = SidecarManager::find_available_port().unwrap();
        assert!(port > 0);
    }

    #[test]
    fn test_check_health_returns_false_for_closed_port() {
        let mgr = SidecarManager {
            binary_path: PathBuf::from("/usr/bin/file-organizer"),
            port: 1, // port 1 is never open in userspace
            state: Arc::new(Mutex::new(SidecarState::Stopped)),
            child: Arc::new(Mutex::new(None)),
            retry_count: Arc::new(Mutex::new(0)),
        };
        assert!(!mgr.check_health());
    }

    #[test]
    fn test_monitor_returns_false_when_no_child() {
        let mgr = SidecarManager {
            binary_path: PathBuf::from("/usr/bin/file-organizer"),
            port: 12347,
            state: Arc::new(Mutex::new(SidecarState::Stopped)),
            child: Arc::new(Mutex::new(None)),
            retry_count: Arc::new(Mutex::new(0)),
        };
        assert!(!mgr.monitor());
    }

    #[test]
    fn test_state_transitions() {
        let mgr = SidecarManager {
            binary_path: PathBuf::from("/usr/bin/file-organizer"),
            port: 12348,
            state: Arc::new(Mutex::new(SidecarState::Stopped)),
            child: Arc::new(Mutex::new(None)),
            retry_count: Arc::new(Mutex::new(0)),
        };
        assert_eq!(mgr.state(), SidecarState::Stopped);

        *mgr.state.lock().unwrap() = SidecarState::Starting;
        assert_eq!(mgr.state(), SidecarState::Starting);

        *mgr.state.lock().unwrap() = SidecarState::Ready;
        assert_eq!(mgr.state(), SidecarState::Ready);

        *mgr.state.lock().unwrap() = SidecarState::Crashed;
        assert_eq!(mgr.state(), SidecarState::Crashed);
    }

    #[test]
    fn test_shutdown_sets_state_to_stopped() {
        let mgr = SidecarManager {
            binary_path: PathBuf::from("/usr/bin/file-organizer"),
            port: 12349,
            state: Arc::new(Mutex::new(SidecarState::Ready)),
            child: Arc::new(Mutex::new(None)),
            retry_count: Arc::new(Mutex::new(0)),
        };
        // shutdown with no child should just update state
        mgr.shutdown();
        assert_eq!(mgr.state(), SidecarState::Stopped);
    }
}
