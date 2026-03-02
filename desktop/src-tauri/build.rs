fn main() {
    // Embed Windows application manifest
    #[cfg(target_os = "windows")]
    {
        let mut res = winres::WindowsResource::new();
        res.set_manifest_file("windows-manifest.xml");
        res.compile().expect("Failed to compile Windows resources");
    }
    // Expose the Rust target triple at compile time so the binary can
    // construct the correct sidecar binary name at runtime.
    println!(
        "cargo:rustc-env=TARGET_TRIPLE={}",
        std::env::var("TARGET").unwrap_or_else(|_| String::from("unknown"))
    );
    tauri_build::build()
}
