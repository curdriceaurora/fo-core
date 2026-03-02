/// Splash screen module — registers the Tauri command for sidecar state queries.
///
/// The splash screen HTML polls this command on load to handle the case where
/// the `sidecar-state` event was emitted before the webview finished initialising.

/// Returns the current sidecar state for the splash screen.
///
/// During normal startup this will return `"starting"`. The splash screen's
/// primary update path is via the `sidecar-state` event; this command exists
/// as a fallback for timing races between Rust and the webview.
#[tauri::command]
pub fn get_sidecar_state() -> serde_json::Value {
    serde_json::json!({
        "state": "starting",
        "port": 0,
        "message": null
    })
}
