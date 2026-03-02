"""Health check endpoints.

The /health route is the authoritative readiness probe for the Tauri sidecar.
The Tauri shell polls this endpoint to determine when the Python backend is ready.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Response

router = APIRouter(tags=["health"])

# Module-level startup time used to compute uptime in health responses.
_startup_time: float = time.time()


@router.get("/health")
async def health(response: Response) -> dict[str, object]:
    """Return rich health status for the API and sidecar readiness probe.

    Response shape::

        {
            "status":    "ok" | "degraded" | "error",
            "readiness": "ready" | "starting" | "unhealthy",
            "version":   "<semver string>",
            "ollama":    true | false,
            "uptime":    <float seconds since startup>
        }

    The ``readiness`` field maps directly from ``status``:

    * ``"ok"``       -> ``"ready"``
    * ``"degraded"`` -> ``"starting"``
    * ``"error"``    -> ``"unhealthy"``

    HTTP status codes:

    * ``200`` – status is "ok"
    * ``207`` – status is "degraded" (backend running but Ollama unreachable)
    * ``503`` – status is "error"
    """
    # Lazy import to avoid circular dependency:
    # service_facade → api.routers → health → service_facade
    from file_organizer.api.service_facade import ServiceFacade

    facade = ServiceFacade()
    try:
        payload = await facade.health_check()
    except Exception:
        payload = {}

    ollama_ok: bool = bool(payload.get("ollama", False))

    if not payload:
        status: str = "error"
    elif ollama_ok:
        status = "ok"
    else:
        status = "degraded"

    _READINESS_MAP: dict[str, str] = {
        "ok": "ready",
        "degraded": "starting",
        "error": "unhealthy",
    }

    result: dict[str, object] = {
        "status": status,
        "readiness": _READINESS_MAP[status],
        "version": payload.get("version", ""),
        "ollama": ollama_ok,
        "uptime": time.time() - _startup_time,
    }

    if status == "degraded":
        response.status_code = 207
    elif status == "error":
        response.status_code = 503
    # "ok" → default 200

    return result
