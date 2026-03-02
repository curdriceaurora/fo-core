"""ServiceFacade: unified entry point for desktop shell integration.

Provides a clean, importable interface for the Tauri desktop shell to interact
with the Python backend without starting the FastAPI server.
"""

from __future__ import annotations

import urllib.request
import urllib.error
from typing import Any

from loguru import logger

from file_organizer.api.config import ApiSettings
from file_organizer.api.routers.config import ConfigResponse
from file_organizer.version import __version__


class ServiceFacade:
    """Unified facade over the file-organizer backend services.

    Designed to be imported and used without starting the HTTP server.
    All methods are async to allow callers (e.g. Tauri IPC bridges) to
    await results without blocking the event loop.

    Example usage::

        facade = ServiceFacade()
        health = await facade.health_check()
        config = await facade.get_config()
    """

    def __init__(self, settings: ApiSettings | None = None) -> None:
        """Initialise the facade with optional API settings.

        Args:
            settings: Optional :class:`~file_organizer.api.config.ApiSettings`
                instance.  When *None* the default settings are used.
        """
        if settings is None:
            settings = ApiSettings()
        self._settings = settings

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """Return a health-check payload for the desktop shell.

        The response always contains:

        * ``status`` – ``"ok"`` when the backend is operational.
        * ``version`` – the package version string.
        * ``ollama`` – ``True`` when the Ollama service is reachable.

        Returns:
            A dictionary with keys ``status`` (str), ``version`` (str) and
            ``ollama`` (bool).
        """
        ollama_ok = await self._check_ollama()
        return {
            "status": "ok",
            "version": __version__,
            "ollama": ollama_ok,
        }

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def get_status(self) -> dict[str, Any]:
        """Return runtime status information.

        Returns a dict containing:

        * ``environment`` – the configured environment name.
        * ``version`` – the package version string.
        * ``auth_enabled`` – whether authentication is active.
        * ``ollama`` – Ollama reachability flag.

        Returns:
            A dictionary with current service status information.
        """
        ollama_ok = await self._check_ollama()
        return {
            "environment": self._settings.environment,
            "version": __version__,
            "auth_enabled": self._settings.auth_enabled,
            "ollama": ollama_ok,
        }

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    async def get_config(self) -> dict[str, Any]:
        """Return the current application configuration.

        Reads from the in-memory config store used by the REST endpoints so
        that the desktop shell always sees the same values as the API.

        Returns:
            A dictionary representation of :class:`~file_organizer.api.routers.config.ConfigResponse`.
        """
        # Import here to avoid circular dependencies at module level
        from file_organizer.api.routers import config as _config_router  # noqa: PLC0415

        cfg: ConfigResponse = _config_router._config  # noqa: SLF001  (intentional)
        return cfg.model_dump()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _check_ollama(self) -> bool:
        """Probe the Ollama service and return ``True`` if it responds.

        Makes a lightweight HTTP request to the Ollama default endpoint.
        Returns ``False`` on any network or HTTP error so the facade never
        raises from a health-check call.

        Returns:
            ``True`` when Ollama is reachable, ``False`` otherwise.
        """
        url = "http://localhost:11434"
        try:
            with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310
                ok: bool = response.status == 200
                return ok
        except Exception as exc:  # noqa: BLE001
            logger.debug("Ollama not reachable: {}", exc)
            return False
