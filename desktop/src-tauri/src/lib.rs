mod daemon;
mod notifications;
mod sidecar;
mod splash;
mod tray;
mod updater;

pub use sidecar::SidecarManager;

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
            tray::create_tray(&app.handle())?;
            notifications::register_notification_listeners(&app.handle());
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
