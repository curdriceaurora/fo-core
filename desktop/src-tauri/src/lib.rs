mod daemon;
mod notifications;
mod sidecar;
mod splash;
mod tray;
mod updater;

pub use sidecar::SidecarManager;

use std::path::PathBuf;
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            splash::get_sidecar_state,
            updater::check_for_updates,
            updater::install_update,
        ])
        .setup(|app| {
            // Resolve sidecar binary path relative to the app resource directory.
            let binary_name = format!(
                "file-organizer-backend-{}",
                std::env::consts::ARCH
            );
            let binary_path = app
                .path()
                .resource_dir()
                .map(|d| d.join("binaries").join(&binary_name))
                .unwrap_or_else(|_| PathBuf::from(&binary_name));

            // Start the sidecar and retrieve the dynamically assigned port.
            let sidecar = SidecarManager::new(binary_path, app.handle().clone())
                .expect("failed to create sidecar manager");
            let port = sidecar.port();

            if let Err(e) = sidecar.start() {
                eprintln!("Warning: sidecar start failed: {e}");
            }

            // Store the sidecar manager in Tauri managed state for later access.
            app.manage(std::sync::Mutex::new(sidecar));

            // Create tray with the dynamic sidecar port.
            tray::create_tray(&app.handle(), port)?;
            notifications::register_notification_listeners(&app.handle());
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
