# File Organizer Desktop

Native desktop shell for File Organizer, built with [Tauri v2](https://tauri.app/).

## Overview

This directory contains the Tauri v2 project that wraps the existing Python FastAPI web UI into a native desktop application. The Tauri shell communicates with a bundled Python sidecar process that serves the FastAPI web server locally.

## Architecture

```
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
