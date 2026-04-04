"""Desktop application wrapper using pywebview.

Launches the existing FastAPI web UI inside a native OS window (WebKit on macOS/Linux,
Edge WebView2 on Windows) with no Electron/Chromium overhead.

Usage::

    python -m file_organizer.desktop

Or via the installed entry-point::

    file-organizer-desktop
"""
