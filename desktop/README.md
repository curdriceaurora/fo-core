# File Organizer Desktop

Native desktop shell for File Organizer, built with [Tauri v2](https://tauri.app/).

## Overview

This directory contains the Tauri v2 project that wraps the existing Python FastAPI web UI into a native desktop application. The Tauri shell communicates with a bundled Python sidecar process that serves the FastAPI web server locally.

## Architecture

```text
desktop/
├── src-tauri/          # Rust/Tauri backend
│   ├── src/
│   │   ├── main.rs     # Entry point (Windows subsystem config)
│   │   └── lib.rs      # App setup, plugin registration, sidecar management
│   ├── capabilities/
│   │   └── default.json  # Tauri permission grants
│   ├── Cargo.toml      # Rust dependencies
│   ├── build.rs        # Tauri build script
│   └── tauri.conf.json # App configuration (window, bundle, security)
├── icons/              # App icons (populated by issue #549)
├── package.json        # Node dev dependencies (tauri-cli)
├── .gitignore          # Excludes build artifacts
└── README.md           # This file
```

## How It Works

- **Development**: The Tauri window loads `http://localhost:8000`, where the FastAPI server must already be running.
- **Production**: The Python backend is bundled as a Tauri sidecar and started automatically. The web UI is served from the sidecar's built-in HTTP server.

## App Configuration

| Setting | Value |
|---------|-------|
| Bundle ID | `com.fileorganizer.app` |
| Window title | `File Organizer` |
| Default size | 1280 × 800 (resizable) |
| Minimum size | 800 × 600 |
| Dev server | `http://localhost:8000` |

## Security

### Content Security Policy (CSP)

The webview CSP in `tauri.conf.json` includes `'unsafe-inline'` for `script-src` and `style-src`. This is required because the Web UI uses Jinja2 server-rendered templates with HTMX, which rely on inline `<script>` tags and `style` attributes. All content is served from the local Python backend (`127.0.0.1`); no remote resources are loaded. Migrating to nonce-based CSP would require refactoring all web templates and is tracked as a future improvement.

### Entitlements (macOS)

The `com.apple.security.cs.allow-unsigned-executable-memory` entitlement is required for the bundled PyInstaller sidecar (loads code from unsigned memory pages) and Ollama's Metal/GPU JIT compilation.

## Auto-Updater

The Tauri updater plugin is included but **disabled** by default (`"active": false` in `tauri.conf.json`). Before enabling it in a release build:

1. Generate a signing key pair: `npx @tauri-apps/cli signer generate -w ~/.tauri/myapp.key`
2. Replace the `"pubkey"` placeholder in `tauri.conf.json` `plugins.updater` with the public key output.
3. Set the `TAURI_SIGNING_PRIVATE_KEY` environment variable in CI so release builds are signed.
4. Set `"active": true` in the updater config.

The `"pubkey"` value ships as a placeholder (`REPLACE_WITH_TAURI_SIGNER_GENERATE_OUTPUT`) and must never be used in production as-is.

## Prerequisites

- [Rust](https://rustup.rs/) 1.70+
- [Node.js](https://nodejs.org/) 18+
- [Tauri system dependencies](https://tauri.app/start/prerequisites/)

## Development

```bash
# Install Node dependencies
cd desktop
npm install

# Start the FastAPI backend first (from project root)
cd ..
pip install -e .
file-organizer api start

# In another terminal, run the Tauri dev build
cd desktop
npm run dev
```

## Building

```bash
cd desktop
npm install
npm run build
```

Binaries will be placed in `src-tauri/target/release/bundle/`.
