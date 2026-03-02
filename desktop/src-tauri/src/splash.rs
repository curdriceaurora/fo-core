/// Splash screen module — registers the Tauri command for sidecar state queries.
///
/// The splash screen HTML polls this command on load to handle the case where
/// the `sidecar-state` event was emitted before the webview finished initialising.

use tauri::Manager;

/// Returns the current sidecar state for the splash screen.
///
/// Queries the actual `SidecarManager` instance stored in Tauri managed state.
/// Falls back to `"starting"` if the manager is not yet available.
#[tauri::command]
pub fn get_sidecar_state(app: tauri::AppHandle) -> serde_json::Value {
    if let Some(mgr) = app.try_state::<std::sync::Mutex<crate::SidecarManager>>() {
        if let Ok(sidecar) = mgr.lock() {
            let state_str = match sidecar.state() {
                crate::sidecar::SidecarState::Stopped => "stopped",
                crate::sidecar::SidecarState::Starting => "starting",
                crate::sidecar::SidecarState::Ready => "ready",
                crate::sidecar::SidecarState::Crashed => "unhealthy",
            };
            return serde_json::json!({
                "state": state_str,
                "port": sidecar.port(),
                "message": null
            });
        }
    }
    serde_json::json!({
        "state": "starting",
        "port": 0,
        "message": null
    })
}
