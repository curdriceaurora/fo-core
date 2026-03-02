"""ServiceFacade: unified entry point for desktop shell integration.

Provides a clean, importable interface for the Tauri desktop shell to interact
with the Python backend without starting the FastAPI server.
"""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.request
from pathlib import Path
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
        result = await facade.organize_files("/home/user/Downloads")
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

        * ``status`` -- ``"ok"`` when the backend is operational.
        * ``version`` -- the package version string.
        * ``ollama`` -- ``True`` when the Ollama service is reachable.

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

        * ``environment`` -- the configured environment name.
        * ``version`` -- the package version string.
        * ``auth_enabled`` -- whether authentication is active.
        * ``ollama`` -- Ollama reachability flag.

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
            A dictionary representation of
            :class:`~file_organizer.api.routers.config.ConfigResponse`.
        """
        # Import here to avoid circular dependencies at module level
        from file_organizer.api.routers import config as _config_router

        cfg: ConfigResponse = _config_router._config
        return cfg.model_dump()

    # ------------------------------------------------------------------
    # organize_files
    # ------------------------------------------------------------------

    async def organize_files(
        self,
        source_dir: str,
        output_dir: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Organize files in *source_dir* using the core FileOrganizer.

        This is a thin async wrapper around
        :class:`~file_organizer.core.organizer.FileOrganizer`.  Business
        logic lives entirely in that class; this method only handles the
        IPC envelope.

        Args:
            source_dir: Absolute path to the directory whose files should
                be organized.
            output_dir: Optional destination directory.  When *None* the
                source directory is used as the output (in-place
                organization).
            dry_run: When ``True`` no files are moved; a simulation report
                is returned instead.

        Returns:
            ``{"success": True, "data": {...}}`` on success, or
            ``{"success": False, "error": "<message>"}`` on failure.
            The ``data`` payload mirrors
            :class:`~file_organizer.core.organizer.OrganizationResult` field
            names.
        """
        try:
            from file_organizer.core.organizer import FileOrganizer

            def _blocking_organize() -> dict[str, Any]:
                organizer = FileOrganizer(dry_run=dry_run)
                dest = output_dir if output_dir is not None else source_dir
                result = organizer.organize(
                    input_path=source_dir,
                    output_path=dest,
                )
                return {
                    "total_files": result.total_files,
                    "processed_files": result.processed_files,
                    "skipped_files": result.skipped_files,
                    "failed_files": result.failed_files,
                    "processing_time": result.processing_time,
                    "organized_structure": result.organized_structure,
                    "errors": result.errors,
                    "dry_run": dry_run,
                }

            data = await asyncio.to_thread(_blocking_organize)
            return {"success": True, "data": data}
        except Exception as exc:
            logger.error("organize_files failed: {}", exc)
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Daemon management
    # ------------------------------------------------------------------

    async def get_daemon_status(self) -> dict[str, Any]:
        """Return the current status of the background daemon service.

        Queries a freshly-constructed
        :class:`~file_organizer.daemon.service.DaemonService` to read its
        initial state.  A newly constructed daemon will report
        ``running=False``; callers should treat this as "no daemon active"
        unless one was started in-process via :meth:`start_daemon`.

        Returns:
            ``{"success": True, "data": {"running": bool, "uptime_seconds":
            float, "files_processed": int}}`` on success, or
            ``{"success": False, "error": "<message>"}`` on failure.
        """
        try:
            from file_organizer.daemon.config import DaemonConfig
            from file_organizer.daemon.service import DaemonService

            def _blocking_status() -> dict[str, Any]:
                config = DaemonConfig()
                daemon = DaemonService(config)
                return {
                    "running": daemon.is_running,
                    "uptime_seconds": daemon.uptime_seconds,
                    "files_processed": daemon.files_processed,
                }

            data = await asyncio.to_thread(_blocking_status)
            return {"success": True, "data": data}
        except Exception as exc:
            logger.error("get_daemon_status failed: {}", exc)
            return {"success": False, "error": str(exc)}

    async def start_daemon(self) -> dict[str, Any]:
        """Start the background daemon service.

        Launches the :class:`~file_organizer.daemon.service.DaemonService`
        in a background thread so the call returns immediately.

        Returns:
            ``{"success": True, "data": {"started": True}}`` when the daemon
            starts successfully, or ``{"success": False, "error": "<message>"}``
            on failure.
        """
        try:
            from file_organizer.daemon.config import DaemonConfig
            from file_organizer.daemon.service import DaemonService

            def _blocking_start() -> None:
                config = DaemonConfig()
                daemon = DaemonService(config)
                daemon.start_background()

            await asyncio.to_thread(_blocking_start)
            return {"success": True, "data": {"started": True}}
        except Exception as exc:
            logger.error("start_daemon failed: {}", exc)
            return {"success": False, "error": str(exc)}

    async def stop_daemon(self) -> dict[str, Any]:
        """Stop the background daemon service.

        Signals the running :class:`~file_organizer.daemon.service.DaemonService`
        to stop and waits briefly for it to terminate.

        Returns:
            ``{"success": True, "data": {"stopped": True}}`` when the daemon
            stops, or ``{"success": False, "error": "<message>"}`` on failure.
        """
        try:
            from file_organizer.daemon.config import DaemonConfig
            from file_organizer.daemon.service import DaemonService

            def _blocking_stop() -> None:
                config = DaemonConfig()
                daemon = DaemonService(config)
                daemon.stop()

            await asyncio.to_thread(_blocking_stop)
            return {"success": True, "data": {"stopped": True}}
        except Exception as exc:
            logger.error("stop_daemon failed: {}", exc)
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Model status
    # ------------------------------------------------------------------

    async def get_model_status(self) -> dict[str, Any]:
        """Return the status of all configured AI models.

        Queries :class:`~file_organizer.models.model_manager.ModelManager`
        for the list of known models and their installation status.

        Returns:
            ``{"success": True, "data": {"models": [...]}}`` where each
            entry contains ``name``, ``model_type``, ``size``,
            ``quantization``, ``description`` and ``installed`` fields.
            Returns ``{"success": False, "error": "<message>"}`` on failure.
        """
        try:
            from file_organizer.models.model_manager import ModelManager

            def _blocking_models() -> list[dict[str, Any]]:
                manager = ModelManager()
                models = manager.list_models()
                return [
                    {
                        "name": m.name,
                        "model_type": m.model_type,
                        "size": m.size,
                        "quantization": m.quantization,
                        "description": m.description,
                        "installed": m.installed,
                    }
                    for m in models
                ]

            model_list = await asyncio.to_thread(_blocking_models)
            return {"success": True, "data": {"models": model_list}}
        except Exception as exc:
            logger.error("get_model_status failed: {}", exc)
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Smart suggestions
    # ------------------------------------------------------------------

    async def get_suggestions(self, path: str) -> dict[str, Any]:
        """Return organization suggestions for a given directory path.

        Wraps :class:`~file_organizer.services.smart_suggestions.SuggestionEngine`
        to analyse the files at *path* and produce ranked placement
        suggestions.

        Args:
            path: Absolute path to a directory whose files should be
                analysed.

        Returns:
            ``{"success": True, "data": {"suggestions": [...]}}`` where each
            entry contains the suggestion details, or
            ``{"success": False, "error": "<message>"}`` on failure.
        """
        try:
            from file_organizer.services.smart_suggestions import SuggestionEngine

            def _blocking_suggestions() -> list[dict[str, Any]]:
                engine = SuggestionEngine()
                target = Path(path)
                files = [p for p in target.rglob("*") if p.is_file()]
                suggestions = engine.generate_suggestions(files)
                return [
                    {
                        "suggestion_type": s.suggestion_type.value
                        if hasattr(s.suggestion_type, "value")
                        else str(s.suggestion_type),
                        "source_path": str(s.file_path),
                        "target_path": str(s.target_path)
                        if hasattr(s, "target_path") and s.target_path is not None
                        else None,
                        "confidence": s.confidence,
                        "reason": s.reasoning if hasattr(s, "reasoning") else "",
                    }
                    for s in suggestions
                ]

            suggestion_list = await asyncio.to_thread(_blocking_suggestions)
            return {"success": True, "data": {"suggestions": suggestion_list}}
        except Exception as exc:
            logger.error("get_suggestions failed: {}", exc)
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    async def find_duplicates(self, scan_dir: str) -> dict[str, Any]:
        """Scan *scan_dir* for duplicate files and return a summary.

        Wraps
        :class:`~file_organizer.services.deduplication.detector.DuplicateDetector`.

        Args:
            scan_dir: Absolute path to the directory to scan.

        Returns:
            ``{"success": True, "data": {"statistics": {...}, "groups": [...]}}``
            on success, or ``{"success": False, "error": "<message>"}`` on
            failure.  ``statistics`` mirrors the output of
            :meth:`~file_organizer.services.deduplication.detector.DuplicateDetector.get_statistics`.
        """
        try:
            from file_organizer.services.deduplication.detector import (
                DuplicateDetector,
            )

            def _blocking_dedup() -> dict[str, Any]:
                detector = DuplicateDetector()
                detector.scan_directory(Path(scan_dir))

                stats = detector.get_statistics()
                groups_raw = detector.get_duplicate_groups()

                # Serialise groups (dict of hash -> DuplicateGroup objects)
                groups: list[dict[str, Any]] = []
                for file_hash, group in groups_raw.items():
                    paths = (
                        [str(fm.path) for fm in group.files]
                        if hasattr(group, "files")
                        else []
                    )
                    groups.append(
                        {
                            "hash": file_hash,
                            "file_count": len(paths),
                            "files": paths,
                        }
                    )

                return {
                    "statistics": stats if isinstance(stats, dict) else vars(stats),
                    "groups": groups,
                }

            data = await asyncio.to_thread(_blocking_dedup)
            return {"success": True, "data": data}
        except Exception as exc:
            logger.error("find_duplicates failed: {}", exc)
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Undo / history
    # ------------------------------------------------------------------

    async def undo_last_operation(self) -> dict[str, Any]:
        """Undo the most recently completed file operation.

        Wraps :class:`~file_organizer.undo.undo_manager.UndoManager`.

        Returns:
            ``{"success": True, "data": {"undone": True}}`` when the undo
            succeeds, ``{"success": True, "data": {"undone": False}}`` when
            there is nothing to undo, or
            ``{"success": False, "error": "<message>"}`` on failure.
        """
        try:
            from file_organizer.undo.undo_manager import UndoManager

            def _blocking_undo() -> bool:
                manager = UndoManager()
                return manager.undo_last_operation()

            undone = await asyncio.to_thread(_blocking_undo)
            return {"success": True, "data": {"undone": undone}}
        except Exception as exc:
            logger.error("undo_last_operation failed: {}", exc)
            return {"success": False, "error": str(exc)}

    async def get_operation_history(self, limit: int = 10) -> dict[str, Any]:
        """Return recent file operation history.

        Wraps :class:`~file_organizer.history.tracker.OperationHistory`.

        Args:
            limit: Maximum number of operations to return (default: 10).

        Returns:
            ``{"success": True, "data": {"operations": [...]}}`` where each
            entry is a serialised
            :class:`~file_organizer.history.tracker.Operation`, or
            ``{"success": False, "error": "<message>"}`` on failure.
        """
        try:
            from file_organizer.history.tracker import OperationHistory

            def _blocking_history() -> list[dict[str, Any]]:
                history = OperationHistory()
                ops = history.get_recent_operations(limit=limit)

                serialised = []
                for op in ops:
                    serialised.append(
                        {
                            "id": op.id,
                            "operation_type": op.operation_type.value
                            if hasattr(op.operation_type, "value")
                            else str(op.operation_type),
                            "source_path": str(op.source_path),
                            "destination_path": str(op.destination_path)
                            if op.destination_path is not None
                            else None,
                            "status": op.status.value
                            if hasattr(op.status, "value")
                            else str(op.status),
                            "timestamp": op.timestamp.isoformat()
                            if hasattr(op.timestamp, "isoformat")
                            else str(op.timestamp),
                        }
                    )
                return serialised

            operations = await asyncio.to_thread(_blocking_history)
            return {"success": True, "data": {"operations": operations}}
        except Exception as exc:
            logger.error("get_operation_history failed: {}", exc)
            return {"success": False, "error": str(exc)}

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

        def _blocking_check() -> bool:
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    return response.status == 200
            except Exception as exc:
                logger.debug("Ollama not reachable: {}", exc)
                return False

        return await asyncio.to_thread(_blocking_check)
