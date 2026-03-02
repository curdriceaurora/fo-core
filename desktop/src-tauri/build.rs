fn main() {
    // Embed Windows application manifest
    #[cfg(target_os = "windows")]
    {
        let mut res = winres::WindowsResource::new();
        res.set_manifest_file("windows-manifest.xml");
        res.compile().expect("Failed to compile Windows resources");
    }
    tauri_build::build()
}
