"""Daemon control endpoints.

Provides REST API endpoints for starting, stopping, and toggling the
background daemon service. Called by the Tauri system tray menu.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/daemon", tags=["daemon"])


@router.post("/toggle")
async def toggle_daemon() -> dict[str, Any]:
    """Toggle the daemon between running and stopped states.

    Returns the new daemon state after toggling.
    """
    from file_organizer.api.service_facade import ServiceFacade

    facade = ServiceFacade()
    status = await facade.get_daemon_status()

    if status.get("success") and status.get("data", {}).get("running"):
        return await facade.stop_daemon()
    return await facade.start_daemon()


@router.post("/start")
async def start_daemon() -> dict[str, Any]:
    """Start the background daemon service."""
    from file_organizer.api.service_facade import ServiceFacade

    facade = ServiceFacade()
    return await facade.start_daemon()


@router.post("/stop")
async def stop_daemon() -> dict[str, Any]:
    """Stop the background daemon service."""
    from file_organizer.api.service_facade import ServiceFacade

    facade = ServiceFacade()
    return await facade.stop_daemon()


@router.get("/status")
async def daemon_status() -> dict[str, Any]:
    """Get the current daemon status."""
    from file_organizer.api.service_facade import ServiceFacade

    facade = ServiceFacade()
    return await facade.get_daemon_status()
