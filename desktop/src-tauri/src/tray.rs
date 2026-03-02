use std::sync::{Arc, Mutex};
use tauri::{
    menu::{MenuBuilder, MenuItemBuilder, SubmenuBuilder},
    tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager, Runtime,
};

/// Shared state for tray menu, including backend port and daemon pause state.
#[derive(Clone)]
pub struct TrayState {
    pub backend_port: Arc<Mutex<u16>>,
    pub daemon_paused: Arc<Mutex<bool>>,
}

impl TrayState {
    pub fn new(port: u16) -> Self {
        Self {
            backend_port: Arc::new(Mutex::new(port)),
            daemon_paused: Arc::new(Mutex::new(false)),
        }
    }

    pub fn api_url(&self, path: &str) -> String {
        let port = *self.backend_port.lock().unwrap();
        format!("http://127.0.0.1:{}{}", port, path)
    }
}

/// Fire-and-forget HTTP POST to the Python backend REST API.
///
/// Errors are logged to stderr rather than silently dropped so that backend
/// connectivity issues surface during development and in log collectors.
fn api_post(url: String) {
    use std::io::Write;
    use std::net::TcpStream;
    use std::time::Duration;

    std::thread::spawn(move || {
        // Parse host:port from URL (e.g. "http://127.0.0.1:8000/api/v1/organize")
        let without_scheme = url.trim_start_matches("http://");
        let mut parts = without_scheme.splitn(2, '/');
        let host_port = parts.next().unwrap_or("");
        let path = parts.next().unwrap_or("");

        let addr = match host_port.parse::<std::net::SocketAddr>() {
            Ok(a) => a,
            Err(e) => {
                eprintln!("[api_post] failed to parse address '{}': {}", host_port, e);
                return;
            }
        };

        let mut stream = match TcpStream::connect_timeout(&addr, Duration::from_secs(2)) {
            Ok(s) => s,
            Err(e) => {
                eprintln!("[api_post] failed to connect to {}: {}", url, e);
                return;
            }
        };

        let request = format!(
            "POST /{} HTTP/1.0\r\nHost: {}\r\nContent-Length: 0\r\n\r\n",
            path, host_port
        );
        if let Err(e) = stream.write_all(request.as_bytes()) {
            eprintln!("[api_post] failed to send request to {}: {}", url, e);
        }
    });
}

/// Build and register the system tray icon with a full menu.
///
/// Menu layout:
///   Organize Now
///   Recent Activity  ▶ (submenu — populated dynamically in future)
///   ─────────────────
///   Pause Daemon / Resume Daemon  (toggles)
///   ─────────────────
///   Show Window
///   Settings
///   About File Organizer
///   ─────────────────
///   Quit
pub fn create_tray<R: Runtime>(app: &AppHandle<R>, port: u16) -> tauri::Result<TrayIcon<R>> {
    let state = TrayState::new(port);

    // ── Menu items ──────────────────────────────────────────────────────────
    let organize = MenuItemBuilder::new("Organize Now")
        .id("organize")
        .build(app)?;

    // Recent Activity submenu (placeholder; items can be updated at runtime)
    let recent_placeholder = MenuItemBuilder::new("No recent activity")
        .id("recent_placeholder")
        .enabled(false)
        .build(app)?;
    let recent = SubmenuBuilder::new(app, "Recent Activity")
        .item(&recent_placeholder)
        .build()?;

    let pause_resume = MenuItemBuilder::new("Pause Daemon")
        .id("pause_resume")
        .build(app)?;

    let show = MenuItemBuilder::new("Show Window")
        .id("show")
        .build(app)?;

    let settings = MenuItemBuilder::new("Settings")
        .id("settings")
        .build(app)?;

    let about = MenuItemBuilder::new("About File Organizer")
        .id("about")
        .build(app)?;

    let quit = MenuItemBuilder::new("Quit")
        .id("quit")
        .build(app)?;

    let menu = MenuBuilder::new(app)
        .item(&organize)
        .item(&recent)
        .separator()
        .item(&pause_resume)
        .separator()
        .item(&show)
        .item(&settings)
        .item(&about)
        .separator()
        .item(&quit)
        .build()?;

    // ── Tray icon ────────────────────────────────────────────────────────────
    let state_for_menu = state.clone();
    let mut builder = TrayIconBuilder::new()
        .menu(&menu)
        .tooltip("File Organizer");

    // Use the default window icon if available; otherwise create the tray
    // without a custom icon to avoid panicking on platforms where no icon
    // is bundled.
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }

    let tray = builder
        .show_menu_on_left_click(false)
        .on_menu_event(move |app, event| {
            let port = *state_for_menu.backend_port.lock().unwrap();

            match event.id().as_ref() {
                "organize" => {
                    api_post(format!(
                        "http://127.0.0.1:{}/api/v1/organize",
                        port
                    ));
                }

                "pause_resume" => {
                    let mut paused = state_for_menu.daemon_paused.lock().unwrap();
                    *paused = !*paused;
                    api_post(format!(
                        "http://127.0.0.1:{}/api/v1/daemon/toggle",
                        port
                    ));
                    // Update menu label to reflect current state
                    if let Some(item) = app
                        .menu()
                        .and_then(|m| m.get("pause_resume"))
                        .and_then(|i| i.as_menuitem().cloned())
                    {
                        let label = if *paused {
                            "Resume Daemon"
                        } else {
                            "Pause Daemon"
                        };
                        let _ = item.set_text(label);
                    }
                }

                "settings" => {
                    if let Some(window) = app.get_webview_window("main") {
                        let _ = window.show();
                        let _ = window.set_focus();
                        let _ = window.eval("window.location.href='/settings'");
                    }
                }

                "about" => {
                    if let Some(window) = app.get_webview_window("main") {
                        let _ = window.show();
                        let _ = window.set_focus();
                    }
                }

                "show" => {
                    if let Some(window) = app.get_webview_window("main") {
                        let _ = window.show();
                        let _ = window.set_focus();
                    }
                }

                "quit" => {
                    // Shut down the sidecar before exiting.
                    if let Some(mgr) = app.try_state::<std::sync::Mutex<crate::SidecarManager>>() {
                        if let Ok(sidecar) = mgr.lock() {
                            sidecar.shutdown();
                        }
                    }
                    app.exit(0);
                }

                _ => {}
            }
        })
        .on_tray_icon_event(|tray, event| {
            // Left-click toggles window visibility
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                let app = tray.app_handle();
                if let Some(window) = app.get_webview_window("main") {
                    if window.is_visible().unwrap_or(false) {
                        let _ = window.hide();
                    } else {
                        let _ = window.show();
                        let _ = window.set_focus();
                    }
                }
            }
        })
        .build(app)?;

    Ok(tray)
}
