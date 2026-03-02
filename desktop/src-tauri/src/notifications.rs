//! Native OS notifications for key File Organizer events.
//!
//! Uses tauri-plugin-notification (already in Cargo.toml).

use tauri::AppHandle;
use tauri_plugin_notification::NotificationExt;

/// Notification templates
#[derive(Debug, Clone)]
pub enum NotificationEvent {
    OrganizationComplete { files_count: usize, folder: String },
    DuplicatesFound { count: usize },
    UpdateAvailable { version: String },
    DaemonStarted,
    DaemonStopped,
}

impl NotificationEvent {
    pub fn title(&self) -> &str {
        match self {
            Self::OrganizationComplete { .. } => "Organization Complete",
            Self::DuplicatesFound { .. } => "Duplicates Detected",
            Self::UpdateAvailable { .. } => "Update Available",
            Self::DaemonStarted => "File Organizer",
            Self::DaemonStopped => "File Organizer",
        }
    }

    pub fn body(&self) -> String {
        match self {
            Self::OrganizationComplete { files_count, folder } => {
                format!("{} files organized in {}", files_count, folder)
            }
            Self::DuplicatesFound { count } => {
                format!("{} duplicate files detected. Click to review.", count)
            }
            Self::UpdateAvailable { version } => {
                format!("Version {} is available. Click to update.", version)
            }
            Self::DaemonStarted => "Background daemon is now running.".to_string(),
            Self::DaemonStopped => "Background daemon has stopped.".to_string(),
        }
    }
}

/// Send a native OS notification
pub fn send_notification<R: tauri::Runtime>(
    app: &AppHandle<R>,
    event: NotificationEvent,
) {
    let title = event.title().to_string();
    let body = event.body();

    // Use tauri-plugin-notification
    let _ = app.notification()
        .builder()
        .title(&title)
        .body(&body)
        .show();
}

/// Register event listeners for backend-emitted notification events
pub fn register_notification_listeners<R: tauri::Runtime>(app: &AppHandle<R>) {
    let app_clone = app.clone();
    app.listen("organization-complete", move |event| {
        if let Ok(payload) = serde_json::from_str::<serde_json::Value>(event.payload()) {
            let files_count = payload["files_count"].as_u64().unwrap_or(0) as usize;
            let folder = payload["folder"].as_str().unwrap_or("").to_string();
            send_notification(
                &app_clone,
                NotificationEvent::OrganizationComplete { files_count, folder },
            );
        }
    });

    let app_clone = app.clone();
    app.listen("duplicates-found", move |event| {
        if let Ok(payload) = serde_json::from_str::<serde_json::Value>(event.payload()) {
            let count = payload["count"].as_u64().unwrap_or(0) as usize;
            send_notification(&app_clone, NotificationEvent::DuplicatesFound { count });
        }
    });

    let app_clone = app.clone();
    app.listen("update-available", move |event| {
        if let Ok(payload) = serde_json::from_str::<serde_json::Value>(event.payload()) {
            let version = payload["version"].as_str().unwrap_or("").to_string();
            send_notification(&app_clone, NotificationEvent::UpdateAvailable { version });
        }
    });

    let app_clone = app.clone();
    app.listen("daemon-started", move |_| {
        send_notification(&app_clone, NotificationEvent::DaemonStarted);
    });

    let app_clone = app.clone();
    app.listen("daemon-stopped", move |_| {
        send_notification(&app_clone, NotificationEvent::DaemonStopped);
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_organization_complete_title() {
        let event = NotificationEvent::OrganizationComplete {
            files_count: 42,
            folder: "Downloads".to_string(),
        };
        assert_eq!(event.title(), "Organization Complete");
    }

    #[test]
    fn test_organization_complete_body_contains_count() {
        let event = NotificationEvent::OrganizationComplete {
            files_count: 42,
            folder: "Downloads".to_string(),
        };
        let body = event.body();
        assert!(body.contains("42"), "Body should contain file count");
        assert!(body.contains("Downloads"), "Body should contain folder");
    }

    #[test]
    fn test_duplicates_found_body() {
        let event = NotificationEvent::DuplicatesFound { count: 5 };
        assert!(event.body().contains("5"));
    }

    #[test]
    fn test_update_available_body_contains_version() {
        let event = NotificationEvent::UpdateAvailable {
            version: "2.1.0".to_string(),
        };
        assert!(event.body().contains("2.1.0"));
    }

    #[test]
    fn test_daemon_started_title() {
        assert_eq!(NotificationEvent::DaemonStarted.title(), "File Organizer");
    }

    #[test]
    fn test_all_events_have_title() {
        let events = vec![
            NotificationEvent::OrganizationComplete { files_count: 1, folder: "test".into() },
            NotificationEvent::DuplicatesFound { count: 1 },
            NotificationEvent::UpdateAvailable { version: "1.0".into() },
            NotificationEvent::DaemonStarted,
            NotificationEvent::DaemonStopped,
        ];
        for event in events {
            assert!(!event.title().is_empty());
            assert!(!event.body().is_empty());
        }
    }
}
