"""Integration tests for integrations and PARA/JD methodology AI modules.

Covers:
- src/file_organizer/integrations/manager.py        (IntegrationManager)
- src/file_organizer/integrations/obsidian.py        (ObsidianIntegration)
- src/file_organizer/integrations/workflow.py        (WorkflowIntegration)
- src/file_organizer/integrations/vscode.py          (VSCodeIntegration)
- src/file_organizer/integrations/browser.py         (BrowserExtensionManager)
- src/file_organizer/methodologies/para/ai/suggestion_engine.py  (PARASuggestionEngine)
- src/file_organizer/methodologies/para/ai/feature_extractor.py  (FeatureExtractor)
- src/file_organizer/methodologies/johnny_decimal/migrator.py     (JohnnyDecimalMigrator)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_file(tmp_path: Path, name: str, content: str = "hello") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def _make_integration_config(name: str, integration_type_str: str, **settings):
    from file_organizer.integrations.base import IntegrationConfig, IntegrationType

    itype = IntegrationType(integration_type_str)
    return IntegrationConfig(name=name, integration_type=itype, settings=settings)


def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ===========================================================================
# IntegrationManager
# ===========================================================================


class TestIntegrationManagerRegister:
    """Tests for register/unregister/get/names."""

    def test_register_and_get_by_name(self) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.manager import IntegrationManager

        manager = IntegrationManager()
        mock_integration = MagicMock()
        mock_integration.config = IntegrationConfig(
            name="obsidian", integration_type=IntegrationType.DESKTOP_APP
        )
        manager.register(mock_integration)

        result = manager.get("obsidian")
        assert result is mock_integration

    def test_get_missing_returns_none(self) -> None:
        from file_organizer.integrations.manager import IntegrationManager

        manager = IntegrationManager()
        result = manager.get("nonexistent")
        assert result is None

    def test_unregister_removes_integration(self) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.manager import IntegrationManager

        manager = IntegrationManager()
        mock_integration = MagicMock()
        mock_integration.config = IntegrationConfig(
            name="vscode", integration_type=IntegrationType.EDITOR
        )
        manager.register(mock_integration)
        manager.unregister("vscode")
        assert manager.get("vscode") is None

    def test_names_returns_sorted_list(self) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.manager import IntegrationManager

        manager = IntegrationManager()
        for name in ("zebra", "alpha", "mango"):
            mi = MagicMock()
            mi.config = IntegrationConfig(name=name, integration_type=IntegrationType.API)
            manager.register(mi)

        names = manager.names()
        assert names == ["alpha", "mango", "zebra"]

    def test_list_configs_returns_all(self) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.manager import IntegrationManager

        manager = IntegrationManager()
        for name in ("b_int", "a_int"):
            mi = MagicMock()
            mi.config = IntegrationConfig(name=name, integration_type=IntegrationType.API)
            manager.register(mi)

        configs = manager.list_configs()
        assert len(configs) == 2
        assert configs[0].name == "a_int"
        assert configs[1].name == "b_int"


class TestIntegrationManagerConnect:
    """Tests for connect/disconnect lifecycle."""

    def test_connect_unknown_integration_returns_false(self) -> None:
        from file_organizer.integrations.manager import IntegrationManager

        manager = IntegrationManager()
        result = _run(manager.connect("nonexistent"))
        assert result is False

    def test_connect_delegates_to_integration(self) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.manager import IntegrationManager

        manager = IntegrationManager()
        mock_integration = MagicMock()
        mock_integration.config = IntegrationConfig(
            name="test", integration_type=IntegrationType.EDITOR
        )
        mock_integration.connect = AsyncMock(return_value=True)
        manager.register(mock_integration)

        result = _run(manager.connect("test"))
        assert result is True
        mock_integration.connect.assert_called_once()

    def test_disconnect_unknown_returns_false(self) -> None:
        from file_organizer.integrations.manager import IntegrationManager

        manager = IntegrationManager()
        result = _run(manager.disconnect("ghost"))
        assert result is False

    def test_disconnect_returns_true(self) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.manager import IntegrationManager

        manager = IntegrationManager()
        mock_integration = MagicMock()
        mock_integration.config = IntegrationConfig(
            name="test", integration_type=IntegrationType.EDITOR
        )
        mock_integration.disconnect = AsyncMock()
        manager.register(mock_integration)

        result = _run(manager.disconnect("test"))
        assert result is True

    def test_connect_all_skips_disabled(self) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.manager import IntegrationManager

        manager = IntegrationManager()
        mi = MagicMock()
        mi.config = IntegrationConfig(
            name="disabled_int", integration_type=IntegrationType.API, enabled=False
        )
        mi.connect = AsyncMock(return_value=True)
        manager.register(mi)

        results = _run(manager.connect_all())
        assert results["disabled_int"] is False
        mi.connect.assert_not_called()

    def test_connect_all_connects_enabled(self) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.manager import IntegrationManager

        manager = IntegrationManager()
        mi = MagicMock()
        mi.config = IntegrationConfig(
            name="enabled_int", integration_type=IntegrationType.API, enabled=True
        )
        mi.connect = AsyncMock(return_value=True)
        manager.register(mi)

        results = _run(manager.connect_all())
        assert results["enabled_int"] is True
        mi.connect.assert_called_once()

    def test_disconnect_all_calls_all(self) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.manager import IntegrationManager

        manager = IntegrationManager()
        disconnects = []
        for name in ("aa", "bb"):
            mi = MagicMock()
            mi.config = IntegrationConfig(name=name, integration_type=IntegrationType.API)
            mi.disconnect = AsyncMock()
            disconnects.append(mi)
            manager.register(mi)

        _run(manager.disconnect_all())
        for mi in disconnects:
            mi.disconnect.assert_called_once()


class TestIntegrationManagerSendFile:
    """Tests for send_file delegation."""

    def test_send_file_unknown_integration_returns_false(self) -> None:
        from file_organizer.integrations.manager import IntegrationManager

        manager = IntegrationManager()
        result = _run(manager.send_file("ghost", "/path/to/file.txt"))
        assert result is False

    def test_send_file_auto_connects_and_delegates(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.manager import IntegrationManager

        manager = IntegrationManager()
        mi = MagicMock()
        mi.config = IntegrationConfig(name="sender", integration_type=IntegrationType.EDITOR)
        mi.connected = False
        mi.connect = AsyncMock(return_value=True)
        mi.send_file = AsyncMock(return_value=True)
        manager.register(mi)

        result = _run(manager.send_file("sender", "/some/file.txt", metadata={"key": "val"}))
        assert result is True
        mi.connect.assert_called_once()
        mi.send_file.assert_called_once_with("/some/file.txt", metadata={"key": "val"})

    def test_send_file_skips_connect_if_already_connected(self) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.manager import IntegrationManager

        manager = IntegrationManager()
        mi = MagicMock()
        mi.config = IntegrationConfig(name="pre_conn", integration_type=IntegrationType.EDITOR)
        mi.connected = True
        mi.connect = AsyncMock(return_value=True)
        mi.send_file = AsyncMock(return_value=True)
        manager.register(mi)

        _run(manager.send_file("pre_conn", "/file.txt"))
        mi.connect.assert_not_called()

    def test_send_file_returns_false_if_connect_fails(self) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.manager import IntegrationManager

        manager = IntegrationManager()
        mi = MagicMock()
        mi.config = IntegrationConfig(name="bad_conn", integration_type=IntegrationType.EDITOR)
        mi.connected = False
        mi.connect = AsyncMock(return_value=False)
        mi.send_file = AsyncMock(return_value=True)
        manager.register(mi)

        result = _run(manager.send_file("bad_conn", "/file.txt"))
        assert result is False
        mi.send_file.assert_not_called()


class TestIntegrationManagerUpdateSettings:
    """Tests for update_settings."""

    def test_update_settings_missing_integration_returns_false(self) -> None:
        from file_organizer.integrations.manager import IntegrationManager

        manager = IntegrationManager()
        result = manager.update_settings("ghost", {"key": "val"})
        assert result is False

    def test_update_settings_merges_and_resets_connected(self) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.manager import IntegrationManager

        manager = IntegrationManager()
        config = IntegrationConfig(
            name="upd", integration_type=IntegrationType.API, settings={"old": "v1"}
        )
        mi = MagicMock()
        mi.config = config
        mi.connected = True
        manager.register(mi)

        result = manager.update_settings("upd", {"new": "v2"})
        assert result is True
        assert mi.connected is False
        assert config.settings["new"] == "v2"
        assert config.settings["old"] == "v1"


# ===========================================================================
# ObsidianIntegration
# ===========================================================================


class TestObsidianIntegration:
    """Tests for ObsidianIntegration lifecycle and send_file."""

    def test_connect_fails_when_vault_missing(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.obsidian import ObsidianIntegration

        config = IntegrationConfig(
            name="obsidian",
            integration_type=IntegrationType.DESKTOP_APP,
            settings={"vault_path": str(tmp_path / "nonexistent_vault")},
        )
        integration = ObsidianIntegration(config)
        result = _run(integration.connect())
        assert result is False
        assert integration.connected is False

    def test_connect_succeeds_when_vault_exists(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.obsidian import ObsidianIntegration

        vault = tmp_path / "my_vault"
        vault.mkdir()
        config = IntegrationConfig(
            name="obsidian",
            integration_type=IntegrationType.DESKTOP_APP,
            settings={"vault_path": str(vault)},
        )
        integration = ObsidianIntegration(config)
        result = _run(integration.connect())
        assert result is True
        assert integration.connected is True

    def test_disconnect_sets_connected_false(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.obsidian import ObsidianIntegration

        vault = tmp_path / "vault"
        vault.mkdir()
        config = IntegrationConfig(
            name="obsidian",
            integration_type=IntegrationType.DESKTOP_APP,
            settings={"vault_path": str(vault)},
        )
        integration = ObsidianIntegration(config)
        _run(integration.connect())
        assert integration.connected is True
        _run(integration.disconnect())
        assert integration.connected is False

    def test_validate_auth_none_method_returns_true(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.obsidian import ObsidianIntegration

        config = IntegrationConfig(
            name="obsidian",
            integration_type=IntegrationType.DESKTOP_APP,
            auth_method="none",
            settings={"vault_path": str(tmp_path)},
        )
        integration = ObsidianIntegration(config)
        result = _run(integration.validate_auth())
        assert result is True

    def test_validate_auth_api_key_missing_returns_false(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.obsidian import ObsidianIntegration

        config = IntegrationConfig(
            name="obsidian",
            integration_type=IntegrationType.DESKTOP_APP,
            auth_method="api_key",
            settings={"vault_path": str(tmp_path), "api_key": ""},
        )
        integration = ObsidianIntegration(config)
        result = _run(integration.validate_auth())
        assert result is False

    def test_validate_auth_api_key_present_returns_true(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.obsidian import ObsidianIntegration

        config = IntegrationConfig(
            name="obsidian",
            integration_type=IntegrationType.DESKTOP_APP,
            auth_method="api_key",
            settings={"vault_path": str(tmp_path), "api_key": "secret123"},
        )
        integration = ObsidianIntegration(config)
        result = _run(integration.validate_auth())
        assert result is True

    def test_send_file_copies_to_attachments_and_creates_note(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.obsidian import ObsidianIntegration

        vault = tmp_path / "vault"
        vault.mkdir()
        source = tmp_path / "report.txt"
        source.write_text("some content", encoding="utf-8")
        config = IntegrationConfig(
            name="obsidian",
            integration_type=IntegrationType.DESKTOP_APP,
            auth_method="none",
            settings={
                "vault_path": str(vault),
                "attachments_subdir": "Attachments",
                "notes_subdir": "Notes",
            },
        )
        integration = ObsidianIntegration(config)
        _run(integration.connect())

        result = _run(integration.send_file(str(source), metadata={"tag": "important"}))
        assert result is True
        assert (vault / "Attachments" / "report.txt").exists()
        note = vault / "Notes" / "report.md"
        assert note.exists()
        note_content = note.read_text(encoding="utf-8")
        assert "source:" in note_content
        assert "exported_at:" in note_content
        assert "# report.txt" in note_content
        assert "tag: important" in note_content

    def test_send_file_returns_false_for_nonexistent_source(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.obsidian import ObsidianIntegration

        vault = tmp_path / "vault"
        vault.mkdir()
        config = IntegrationConfig(
            name="obsidian",
            integration_type=IntegrationType.DESKTOP_APP,
            auth_method="none",
            settings={"vault_path": str(vault)},
        )
        integration = ObsidianIntegration(config)
        _run(integration.connect())

        result = _run(integration.send_file(str(tmp_path / "missing.txt")))
        assert result is False

    def test_get_status_reflects_vault_state(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.obsidian import ObsidianIntegration

        vault = tmp_path / "vault"
        vault.mkdir()
        config = IntegrationConfig(
            name="obsidian",
            integration_type=IntegrationType.DESKTOP_APP,
            settings={"vault_path": str(vault)},
        )
        integration = ObsidianIntegration(config)
        _run(integration.connect())

        status = _run(integration.get_status())
        assert status.name == "obsidian"
        assert status.connected is True
        assert status.details["vault_exists"] is True
        assert status.details["auth_method"] == "none"

    def test_type_forced_to_desktop_app(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.obsidian import ObsidianIntegration

        config = IntegrationConfig(
            name="obsidian",
            integration_type=IntegrationType.API,  # wrong type — should be corrected
            settings={"vault_path": str(tmp_path)},
        )
        ObsidianIntegration(config)
        assert config.integration_type is IntegrationType.DESKTOP_APP

    def test_send_file_with_no_metadata_note_has_no_metadata_key(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.obsidian import ObsidianIntegration

        vault = tmp_path / "vault"
        vault.mkdir()
        source = tmp_path / "plain.txt"
        source.write_text("hello", encoding="utf-8")
        config = IntegrationConfig(
            name="obsidian",
            integration_type=IntegrationType.DESKTOP_APP,
            auth_method="none",
            settings={"vault_path": str(vault)},
        )
        integration = ObsidianIntegration(config)
        _run(integration.connect())
        result = _run(integration.send_file(str(source)))
        assert result is True
        note = vault / "Notes" / "plain.md"
        note_content = note.read_text(encoding="utf-8")
        assert "metadata:" not in note_content


# ===========================================================================
# WorkflowIntegration
# ===========================================================================


class TestWorkflowIntegration:
    """Tests for WorkflowIntegration (Alfred/Raycast payloads)."""

    def test_connect_creates_output_dir(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.workflow import WorkflowIntegration

        out_dir = tmp_path / "workflow_out"
        config = IntegrationConfig(
            name="workflow",
            integration_type=IntegrationType.WORKFLOW,
            settings={"output_dir": str(out_dir)},
        )
        integration = WorkflowIntegration(config)
        result = _run(integration.connect())
        assert result is True
        assert out_dir.is_dir()

    def test_disconnect_sets_connected_false(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.workflow import WorkflowIntegration

        out_dir = tmp_path / "wf_out"
        config = IntegrationConfig(
            name="workflow",
            integration_type=IntegrationType.WORKFLOW,
            settings={"output_dir": str(out_dir)},
        )
        integration = WorkflowIntegration(config)
        _run(integration.connect())
        _run(integration.disconnect())
        assert integration.connected is False

    def test_validate_auth_always_true(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.workflow import WorkflowIntegration

        config = IntegrationConfig(
            name="workflow",
            integration_type=IntegrationType.WORKFLOW,
            settings={"output_dir": str(tmp_path)},
        )
        integration = WorkflowIntegration(config)
        assert _run(integration.validate_auth()) is True

    def test_send_file_creates_alfred_and_raycast_json(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.workflow import WorkflowIntegration

        out_dir = tmp_path / "wf_out"
        source = tmp_path / "notes.md"
        source.write_text("my notes", encoding="utf-8")
        config = IntegrationConfig(
            name="workflow",
            integration_type=IntegrationType.WORKFLOW,
            settings={"output_dir": str(out_dir)},
        )
        integration = WorkflowIntegration(config)
        _run(integration.connect())

        result = _run(integration.send_file(str(source), metadata={"summary": "quick notes"}))
        assert result is True

        json_files = list(out_dir.glob("*.json"))
        assert len(json_files) == 2

        alfred_files = [f for f in json_files if f.name.startswith("alfred-")]
        raycast_files = [f for f in json_files if f.name.startswith("raycast-")]
        assert len(alfred_files) == 1
        assert len(raycast_files) == 1

        alfred_payload = json.loads(alfred_files[0].read_text(encoding="utf-8"))
        assert "items" in alfred_payload
        assert alfred_payload["items"][0]["title"] == "notes.md"
        assert alfred_payload["items"][0]["subtitle"] == "quick notes"

        raycast_payload = json.loads(raycast_files[0].read_text(encoding="utf-8"))
        assert raycast_payload["name"] == "Open notes.md"
        assert "generated_at" in raycast_payload

    def test_send_file_returns_false_for_missing_source(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.workflow import WorkflowIntegration

        out_dir = tmp_path / "wf_out"
        config = IntegrationConfig(
            name="workflow",
            integration_type=IntegrationType.WORKFLOW,
            settings={"output_dir": str(out_dir)},
        )
        integration = WorkflowIntegration(config)
        _run(integration.connect())

        result = _run(integration.send_file(str(tmp_path / "does_not_exist.txt")))
        assert result is False

    def test_alfred_payload_uid_contains_stem(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.workflow import WorkflowIntegration

        out_dir = tmp_path / "wf_out"
        source = tmp_path / "myfile.txt"
        source.write_text("content", encoding="utf-8")
        config = IntegrationConfig(
            name="workflow",
            integration_type=IntegrationType.WORKFLOW,
            settings={"output_dir": str(out_dir)},
        )
        integration = WorkflowIntegration(config)
        _run(integration.connect())
        _run(integration.send_file(str(source)))

        alfred_file = next(out_dir.glob("alfred-*.json"))
        payload = json.loads(alfred_file.read_text(encoding="utf-8"))
        uid = payload["items"][0]["uid"]
        assert uid.startswith("myfile-")

    def test_get_status_reports_output_dir(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.workflow import WorkflowIntegration

        out_dir = tmp_path / "wf_out"
        config = IntegrationConfig(
            name="workflow",
            integration_type=IntegrationType.WORKFLOW,
            settings={"output_dir": str(out_dir)},
        )
        integration = WorkflowIntegration(config)
        _run(integration.connect())

        status = _run(integration.get_status())
        assert status.name == "workflow"
        assert str(out_dir) in status.details["output_dir"]
        assert status.details["output_exists"] is True

    def test_type_forced_to_workflow(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.workflow import WorkflowIntegration

        config = IntegrationConfig(
            name="workflow",
            integration_type=IntegrationType.API,
            settings={"output_dir": str(tmp_path)},
        )
        WorkflowIntegration(config)
        assert config.integration_type is IntegrationType.WORKFLOW

    def test_send_file_default_subtitle_when_no_summary(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.workflow import WorkflowIntegration

        out_dir = tmp_path / "wf_out"
        source = tmp_path / "doc.txt"
        source.write_text("hello", encoding="utf-8")
        config = IntegrationConfig(
            name="workflow",
            integration_type=IntegrationType.WORKFLOW,
            settings={"output_dir": str(out_dir)},
        )
        integration = WorkflowIntegration(config)
        _run(integration.connect())
        _run(integration.send_file(str(source)))

        alfred_file = next(out_dir.glob("alfred-*.json"))
        payload = json.loads(alfred_file.read_text(encoding="utf-8"))
        assert payload["items"][0]["subtitle"] == "File exported by File Organizer"


# ===========================================================================
# VSCodeIntegration
# ===========================================================================


class TestVSCodeIntegration:
    """Tests for VSCodeIntegration."""

    def test_connect_no_workspace_always_succeeds(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.vscode import VSCodeIntegration

        config = IntegrationConfig(
            name="vscode",
            integration_type=IntegrationType.EDITOR,
            settings={},  # no workspace_path → None → always connected
        )
        integration = VSCodeIntegration(config)
        result = _run(integration.connect())
        assert result is True
        assert integration.connected is True

    def test_connect_fails_when_workspace_missing(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.vscode import VSCodeIntegration

        config = IntegrationConfig(
            name="vscode",
            integration_type=IntegrationType.EDITOR,
            settings={"workspace_path": str(tmp_path / "missing_ws")},
        )
        integration = VSCodeIntegration(config)
        result = _run(integration.connect())
        assert result is False

    def test_connect_succeeds_when_workspace_exists(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.vscode import VSCodeIntegration

        ws = tmp_path / "workspace"
        ws.mkdir()
        config = IntegrationConfig(
            name="vscode",
            integration_type=IntegrationType.EDITOR,
            settings={"workspace_path": str(ws)},
        )
        integration = VSCodeIntegration(config)
        result = _run(integration.connect())
        assert result is True

    def test_disconnect_sets_connected_false(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.vscode import VSCodeIntegration

        config = IntegrationConfig(
            name="vscode",
            integration_type=IntegrationType.EDITOR,
            settings={},
        )
        integration = VSCodeIntegration(config)
        _run(integration.connect())
        _run(integration.disconnect())
        assert integration.connected is False

    def test_validate_auth_always_true(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.vscode import VSCodeIntegration

        config = IntegrationConfig(
            name="vscode",
            integration_type=IntegrationType.EDITOR,
            settings={},
        )
        integration = VSCodeIntegration(config)
        assert _run(integration.validate_auth()) is True

    def test_send_file_appends_jsonl_entry(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.vscode import VSCodeIntegration

        cmd_output = tmp_path / "commands.jsonl"
        source = tmp_path / "main.py"
        source.write_text("print('hello')", encoding="utf-8")
        config = IntegrationConfig(
            name="vscode",
            integration_type=IntegrationType.EDITOR,
            settings={"command_output_path": str(cmd_output)},
        )
        integration = VSCodeIntegration(config)
        _run(integration.connect())

        result = _run(integration.send_file(str(source), metadata={"line": 42}))
        assert result is True
        assert cmd_output.exists()
        lines = cmd_output.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["command"] == "vscode.open"
        assert entry["uri"].startswith("vscode://file/")
        assert entry["metadata"] == {"line": 42}
        assert "created_at" in entry

    def test_send_file_appends_multiple_entries(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.vscode import VSCodeIntegration

        cmd_output = tmp_path / "commands.jsonl"
        config = IntegrationConfig(
            name="vscode",
            integration_type=IntegrationType.EDITOR,
            settings={"command_output_path": str(cmd_output)},
        )
        integration = VSCodeIntegration(config)
        _run(integration.connect())

        for i in range(3):
            f = tmp_path / f"file{i}.txt"
            f.write_text(f"content {i}", encoding="utf-8")
            _run(integration.send_file(str(f)))

        lines = cmd_output.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3

    def test_send_file_returns_false_for_directory(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.vscode import VSCodeIntegration

        cmd_output = tmp_path / "commands.jsonl"
        a_dir = tmp_path / "a_dir"
        a_dir.mkdir()
        config = IntegrationConfig(
            name="vscode",
            integration_type=IntegrationType.EDITOR,
            settings={"command_output_path": str(cmd_output)},
        )
        integration = VSCodeIntegration(config)
        _run(integration.connect())
        result = _run(integration.send_file(str(a_dir)))
        assert result is False

    def test_get_status_workspace_none_sets_exists_true(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.vscode import VSCodeIntegration

        config = IntegrationConfig(
            name="vscode",
            integration_type=IntegrationType.EDITOR,
            settings={},
        )
        integration = VSCodeIntegration(config)
        _run(integration.connect())
        status = _run(integration.get_status())
        assert status.details["workspace_path"] is None
        assert status.details["workspace_exists"] is True

    def test_type_forced_to_editor(self, tmp_path: Path) -> None:
        from file_organizer.integrations.base import IntegrationConfig, IntegrationType
        from file_organizer.integrations.vscode import VSCodeIntegration

        config = IntegrationConfig(
            name="vscode",
            integration_type=IntegrationType.API,
            settings={},
        )
        VSCodeIntegration(config)
        assert config.integration_type is IntegrationType.EDITOR


# ===========================================================================
# BrowserExtensionManager
# ===========================================================================


class TestBrowserExtensionManager:
    """Tests for BrowserExtensionManager token lifecycle."""

    def test_get_config_returns_origins_and_ttl(self) -> None:
        from file_organizer.integrations.browser import BrowserExtensionManager

        mgr = BrowserExtensionManager(
            allowed_origins=["https://app.example.com"], token_ttl_seconds=1800
        )
        cfg = mgr.get_config()
        assert cfg["allowed_origins"] == ["https://app.example.com"]
        assert cfg["token_ttl_seconds"] == 1800

    def test_issue_token_returns_record_with_extension_id(self) -> None:
        from file_organizer.integrations.browser import BrowserExtensionManager

        mgr = BrowserExtensionManager(allowed_origins=["https://example.com"])
        record = mgr.issue_token("ext-001")
        assert record.extension_id == "ext-001"
        assert len(record.token) > 10
        assert record.expires_at > record.created_at

    def test_verify_token_valid(self) -> None:
        from file_organizer.integrations.browser import BrowserExtensionManager

        mgr = BrowserExtensionManager(allowed_origins=["https://example.com"])
        record = mgr.issue_token("ext-002")
        assert mgr.verify_token(record.token) is True

    def test_verify_token_unknown_returns_false(self) -> None:
        from file_organizer.integrations.browser import BrowserExtensionManager

        mgr = BrowserExtensionManager(allowed_origins=["https://example.com"])
        assert mgr.verify_token("totally-fake-token") is False

    def test_duplicate_origins_are_deduplicated(self) -> None:
        from file_organizer.integrations.browser import BrowserExtensionManager

        mgr = BrowserExtensionManager(
            allowed_origins=["https://a.com", "https://a.com", "https://b.com"]
        )
        cfg = mgr.get_config()
        assert len(cfg["allowed_origins"]) == 2
        assert "https://a.com" in cfg["allowed_origins"]
        assert "https://b.com" in cfg["allowed_origins"]

    def test_expired_tokens_are_pruned_on_verify(self) -> None:
        from datetime import UTC, timedelta

        from file_organizer.integrations.browser import BrowserExtensionManager, BrowserTokenRecord

        mgr = BrowserExtensionManager(allowed_origins=["https://example.com"], token_ttl_seconds=1)
        record = mgr.issue_token("ext-003")

        # Manually expire the token by overwriting its record with one already expired
        from datetime import datetime as dt

        past = dt.now(UTC) - timedelta(seconds=10)
        expired_record = BrowserTokenRecord(
            token=record.token,
            extension_id="ext-003",
            created_at=past,
            expires_at=past,
        )
        with mgr._lock:
            mgr._tokens[record.token] = expired_record

        assert mgr.verify_token(record.token) is False

    def test_default_ttl_is_3600(self) -> None:
        from file_organizer.integrations.browser import BrowserExtensionManager

        mgr = BrowserExtensionManager(allowed_origins=[])
        cfg = mgr.get_config()
        assert cfg["token_ttl_seconds"] == 3600

    def test_multiple_tokens_issued_for_same_extension(self) -> None:
        from file_organizer.integrations.browser import BrowserExtensionManager

        mgr = BrowserExtensionManager(allowed_origins=["https://example.com"])
        r1 = mgr.issue_token("ext-multi")
        r2 = mgr.issue_token("ext-multi")
        assert r1.token != r2.token
        assert mgr.verify_token(r1.token) is True
        assert mgr.verify_token(r2.token) is True

    def test_token_record_has_correct_ttl_window(self) -> None:
        from file_organizer.integrations.browser import BrowserExtensionManager

        mgr = BrowserExtensionManager(allowed_origins=[], token_ttl_seconds=300)
        record = mgr.issue_token("ext-ttl")
        delta = (record.expires_at - record.created_at).total_seconds()
        assert abs(delta - 300) < 2  # within 2s tolerance


# ===========================================================================
# FeatureExtractor (additional coverage)
# ===========================================================================


class TestFeatureExtractorAdditional:
    """Additional coverage for FeatureExtractor paths not covered by existing tests."""

    def test_extract_text_features_temporal_indicators(self) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        content = "Project due date: 2024-03-15. Q1 2024 sprint milestone."
        result = fe.extract_text_features(content)
        assert len(result.temporal_indicators) >= 1

    def test_extract_text_features_action_items_found(self) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        content = "- [ ] TODO: finish report\n- [x] Review PR\nACTION ITEM: deploy"
        result = fe.extract_text_features(content)
        assert len(result.action_items) >= 1

    def test_detect_document_type_plan(self) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        content = "This roadmap outlines the project plan and timeline strategy."
        result = fe.extract_text_features(content)
        assert result.document_type == "plan"

    def test_detect_document_type_reference(self) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        content = "This is a reference guide and documentation manual handbook."
        result = fe.extract_text_features(content)
        assert result.document_type == "reference"

    def test_extract_metadata_features_nonexistent_file(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        missing = tmp_path / "ghost.pdf"
        result = fe.extract_metadata_features(missing)
        assert result.file_type == ".pdf"
        assert result.file_size == 0

    def test_extract_metadata_features_real_file(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        f = _make_file(tmp_path, "sample.txt", content="hello world " * 100)
        result = fe.extract_metadata_features(f)
        assert result.file_type == ".txt"
        assert result.file_size > 0
        assert result.modification_date is not None
        assert result.days_since_modified >= 0.0

    def test_extract_structural_features_depth(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        f = nested / "file.txt"
        f.write_text("x", encoding="utf-8")
        result = fe.extract_structural_features(f)
        assert result.directory_depth >= 3

    def test_extract_structural_features_parent_hint_project(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        project_dir = tmp_path / "projects"
        project_dir.mkdir()
        f = project_dir / "spec.txt"
        f.write_text("content", encoding="utf-8")
        result = fe.extract_structural_features(f)
        assert result.parent_category_hint == "project"

    def test_extract_structural_features_has_date_in_path(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        dated_dir = tmp_path / "2024-01-15"
        dated_dir.mkdir()
        f = dated_dir / "notes.md"
        f.write_text("meeting notes", encoding="utf-8")
        result = fe.extract_structural_features(f)
        assert result.has_date_in_path is True

    def test_extract_structural_features_project_structure_detected(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        (project_dir / "README.md").write_text("# Project", encoding="utf-8")
        f = project_dir / "main.py"
        f.write_text("print('hi')", encoding="utf-8")
        result = fe.extract_structural_features(f)
        assert result.has_project_structure is True

    def test_content_truncation_respected(self) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor(max_content_length=50)
        long_content = "deadline milestone " * 1000
        result = fe.extract_text_features(long_content)
        # word_count only covers truncated portion
        assert result.word_count <= 10  # 50 chars ~ 2-4 words of "deadline milestone"


# ===========================================================================
# PARASuggestionEngine (additional coverage)
# ===========================================================================


class TestPARASuggestionEngineAdditional:
    """Additional coverage for PARASuggestionEngine paths."""

    def _make_mock_engine(self):
        """Build a mocked PARASuggestionEngine with controllable heuristics."""
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor
        from file_organizer.methodologies.para.ai.suggestion_engine import PARASuggestionEngine

        mock_heuristic_engine = MagicMock()
        return PARASuggestionEngine(
            heuristic_engine=mock_heuristic_engine,
            feature_extractor=FeatureExtractor(),
        )

    def _make_heuristic_result(self, scores_map):
        """Helper to build a HeuristicResult from a scores dict."""
        from file_organizer.methodologies.para.ai.suggestion_engine import PARACategory
        from file_organizer.methodologies.para.detection.heuristics import (
            CategoryScore,
            HeuristicResult,
        )

        scores = {}
        for cat in PARACategory:
            raw = scores_map.get(cat, 0.0)
            scores[cat] = CategoryScore(category=cat, score=raw, confidence=raw, signals=[])
        return HeuristicResult(
            scores=scores, overall_confidence=max(scores_map.values(), default=0.0)
        )

    def test_suggest_returns_paracategory(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            PARACategory,
            PARASuggestionEngine,
        )
        from file_organizer.methodologies.para.detection.heuristics import (
            CategoryScore,
            HeuristicResult,
        )

        mock_he = MagicMock()
        scores = {
            cat: CategoryScore(category=cat, score=0.0, confidence=0.0, signals=[])
            for cat in PARACategory
        }
        scores[PARACategory.RESOURCE] = CategoryScore(
            category=PARACategory.RESOURCE, score=0.8, confidence=0.8, signals=["file type"]
        )
        mock_he.evaluate.return_value = HeuristicResult(scores=scores, overall_confidence=0.8)

        engine = PARASuggestionEngine(heuristic_engine=mock_he)
        f = _make_file(tmp_path, "guide.md", content="reference guide manual documentation")
        suggestion = engine.suggest(f)
        assert suggestion.category in list(PARACategory)
        assert 0.0 <= suggestion.confidence <= 1.0

    def test_suggest_with_content_uses_text_features(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            PARACategory,
            PARASuggestionEngine,
        )
        from file_organizer.methodologies.para.detection.heuristics import (
            CategoryScore,
            HeuristicResult,
        )

        mock_he = MagicMock()
        scores = {
            cat: CategoryScore(category=cat, score=0.0, confidence=0.0, signals=[])
            for cat in PARACategory
        }
        mock_he.evaluate.return_value = HeuristicResult(scores=scores, overall_confidence=0.0)

        engine = PARASuggestionEngine(heuristic_engine=mock_he)
        f = _make_file(tmp_path, "todo.md", content="deadline milestone sprint deliverable")
        suggestion = engine.suggest(f, content="deadline milestone sprint deliverable")
        # Text features should boost PROJECT
        assert len(suggestion.reasoning) >= 1

    def test_confidence_label_high(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            PARACategory,
            PARASuggestion,
        )

        s = PARASuggestion(category=PARACategory.PROJECT, confidence=0.9)
        assert s.confidence_label == "High"

    def test_confidence_label_medium(self) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            PARACategory,
            PARASuggestion,
        )

        s = PARASuggestion(category=PARACategory.AREA, confidence=0.65)
        assert s.confidence_label == "Medium"

    def test_confidence_label_low(self) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            PARACategory,
            PARASuggestion,
        )

        s = PARASuggestion(category=PARACategory.RESOURCE, confidence=0.45)
        assert s.confidence_label == "Low"

    def test_confidence_label_very_low(self) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            PARACategory,
            PARASuggestion,
        )

        s = PARASuggestion(category=PARACategory.ARCHIVE, confidence=0.1)
        assert s.confidence_label == "Very Low"

    def test_requires_review_true_for_low_confidence(self) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            PARACategory,
            PARASuggestion,
        )

        s = PARASuggestion(category=PARACategory.PROJECT, confidence=0.3)
        assert s.requires_review is True

    def test_requires_review_false_for_high_confidence(self) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            PARACategory,
            PARASuggestion,
        )

        s = PARASuggestion(category=PARACategory.PROJECT, confidence=0.75)
        assert s.requires_review is False

    def test_invalid_confidence_raises_value_error(self) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            PARACategory,
            PARASuggestion,
        )

        with pytest.raises(ValueError, match="confidence"):
            PARASuggestion(category=PARACategory.PROJECT, confidence=1.5)

    def test_explain_outputs_category_and_confidence(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            PARACategory,
            PARASuggestionEngine,
        )
        from file_organizer.methodologies.para.detection.heuristics import (
            CategoryScore,
            HeuristicResult,
        )

        mock_he = MagicMock()
        scores = {
            cat: CategoryScore(category=cat, score=0.0, confidence=0.0, signals=[])
            for cat in PARACategory
        }
        scores[PARACategory.ARCHIVE] = CategoryScore(
            category=PARACategory.ARCHIVE, score=0.9, confidence=0.9, signals=["old"]
        )
        mock_he.evaluate.return_value = HeuristicResult(scores=scores, overall_confidence=0.9)

        engine = PARASuggestionEngine(heuristic_engine=mock_he)
        f = _make_file(tmp_path, "old_backup.txt", "final completed archived")
        suggestion = engine.suggest(f)
        explanation = engine.explain(suggestion)
        assert "Recommended category:" in explanation
        assert "Confidence:" in explanation
        assert "Reasoning:" in explanation

    def test_explain_includes_alternatives_when_present(self) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            PARACategory,
            PARASuggestion,
            PARASuggestionEngine,
        )

        engine = PARASuggestionEngine.__new__(PARASuggestionEngine)
        suggestion = PARASuggestion(
            category=PARACategory.PROJECT,
            confidence=0.8,
            reasoning=["Heuristic matched"],
            alternative_categories=[(PARACategory.AREA, 0.3)],
            suggested_subfolder="2024-Q1",
            tags=["milestone"],
        )
        explanation = engine.explain(suggestion)
        assert "Alternatives:" in explanation
        assert "Suggested subfolder:" in explanation
        assert "Tags:" in explanation

    def test_suggest_batch_returns_one_per_file(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            PARACategory,
            PARASuggestionEngine,
        )
        from file_organizer.methodologies.para.detection.heuristics import (
            CategoryScore,
            HeuristicResult,
        )

        mock_he = MagicMock()
        scores = {
            cat: CategoryScore(category=cat, score=0.0, confidence=0.0, signals=[])
            for cat in PARACategory
        }
        mock_he.evaluate.return_value = HeuristicResult(scores=scores, overall_confidence=0.0)

        engine = PARASuggestionEngine(heuristic_engine=mock_he)
        files = [_make_file(tmp_path, f"f{i}.txt", "content") for i in range(3)]
        results = engine.suggest_batch(files)
        assert len(results) == 3
        for r in results:
            assert r.category in list(PARACategory)

    def test_suggest_batch_fallback_on_error(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            PARACategory,
            PARASuggestionEngine,
        )

        engine = PARASuggestionEngine.__new__(PARASuggestionEngine)
        # Patch suggest() itself to raise so suggest_batch fallback is exercised
        engine.suggest = MagicMock(side_effect=RuntimeError("suggest broken"))
        f = tmp_path / "broken.txt"
        f.write_text("content", encoding="utf-8")
        results = engine.suggest_batch([f])
        assert len(results) == 1
        assert results[0].category == PARACategory.RESOURCE
        assert results[0].confidence == 0.1
        assert len(results[0].reasoning) == 1
        assert "Error during analysis" in results[0].reasoning[0]

    def test_combine_scores_uses_60_40_weighting(self) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            PARACategory,
            PARASuggestionEngine,
        )

        engine = PARASuggestionEngine.__new__(PARASuggestionEngine)
        heuristic_scores = {
            PARACategory.PROJECT: 1.0,
            PARACategory.AREA: 0.0,
            PARACategory.RESOURCE: 0.0,
            PARACategory.ARCHIVE: 0.0,
        }
        feature_scores = {
            PARACategory.PROJECT: 0.0,
            PARACategory.AREA: 1.0,
            PARACategory.RESOURCE: 0.0,
            PARACategory.ARCHIVE: 0.0,
        }
        combined = engine._combine_scores(heuristic_scores, feature_scores)
        assert abs(combined[PARACategory.PROJECT] - 0.60) < 0.001
        assert abs(combined[PARACategory.AREA] - 0.40) < 0.001


# ===========================================================================
# JohnnyDecimalMigrator
# ===========================================================================


class TestJohnnyDecimalMigratorDryRun:
    """Tests for JohnnyDecimalMigrator dry-run and plan generation."""

    def _make_migrator(self):
        from file_organizer.methodologies.johnny_decimal.categories import get_default_scheme
        from file_organizer.methodologies.johnny_decimal.migrator import JohnnyDecimalMigrator

        return JohnnyDecimalMigrator(scheme=get_default_scheme())

    def _make_simple_plan(self, tmp_path: Path):
        """Build a TransformationPlan with a single rename rule."""
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from file_organizer.methodologies.johnny_decimal.transformer import (
            TransformationPlan,
            TransformationRule,
        )

        folder = tmp_path / "Documents"
        folder.mkdir(exist_ok=True)

        jd_num = JohnnyDecimalNumber(area=10, category=11)
        rule = TransformationRule(
            source_path=folder,
            target_name="11 Documents",
            jd_number=jd_num,
            action="rename",
            confidence=0.9,
        )
        return TransformationPlan(
            root_path=tmp_path,
            rules=[rule],
            estimated_changes=1,
        )

    def test_dry_run_returns_migration_result(self, tmp_path: Path) -> None:
        migrator = self._make_migrator()
        plan = self._make_simple_plan(tmp_path)
        result = migrator.execute_migration(plan, dry_run=True, create_backup=False)
        assert result.success is True
        assert result.transformed_count == 1
        assert result.failed_count == 0
        assert result.backup_path is None

    def test_dry_run_does_not_rename_folders(self, tmp_path: Path) -> None:
        migrator = self._make_migrator()
        plan = self._make_simple_plan(tmp_path)
        migrator.execute_migration(plan, dry_run=True, create_backup=False)
        # Original folder must still exist
        assert (tmp_path / "Documents").exists()
        assert not (tmp_path / "11 Documents").exists()

    def test_generate_report_success(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.migrator import MigrationResult

        migrator = self._make_migrator()
        result = MigrationResult(
            success=True,
            transformed_count=5,
            failed_count=0,
            skipped_count=1,
            duration_seconds=0.42,
        )
        report = migrator.generate_report(result)
        assert "SUCCESS" in report
        assert "5" in report
        assert "0.42" in report

    def test_generate_report_failure_lists_failures(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.migrator import MigrationResult

        migrator = self._make_migrator()
        result = MigrationResult(
            success=False,
            transformed_count=2,
            failed_count=1,
            skipped_count=0,
            duration_seconds=1.0,
            failed_paths=[(tmp_path / "bad_folder", "permission denied")],
        )
        report = migrator.generate_report(result)
        assert "FAILED" in report
        assert "permission denied" in report

    def test_generate_preview_includes_scan_info(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.scanner import ScanResult

        migrator = self._make_migrator()
        plan = self._make_simple_plan(tmp_path)
        scan_result = ScanResult(
            root_path=tmp_path,
            total_folders=3,
            total_files=10,
            total_size=1024 * 512,
            max_depth=2,
            folder_tree=[],
        )
        preview = migrator.generate_preview(plan, scan_result)
        assert "Johnny Decimal Migration Preview" in preview
        assert "3" in preview  # total_folders
        assert "10" in preview  # total_files

    def test_generate_preview_with_validation(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.scanner import ScanResult
        from file_organizer.methodologies.johnny_decimal.validator import ValidationResult

        migrator = self._make_migrator()
        plan = self._make_simple_plan(tmp_path)
        scan_result = ScanResult(
            root_path=tmp_path,
            total_folders=1,
            total_files=2,
            total_size=1024,
            max_depth=1,
            folder_tree=[],
        )
        validation = ValidationResult(is_valid=True)
        preview = migrator.generate_preview(plan, scan_result, validation=validation)
        assert "Validation" in preview
        assert "VALID" in preview

    def test_validate_plan_returns_valid_result(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.transformer import TransformationPlan

        migrator = self._make_migrator()
        plan = TransformationPlan(root_path=tmp_path, rules=[], estimated_changes=0)
        result = migrator.validate_plan(plan)
        # Empty plan should be valid (no rules to conflict)
        assert result.is_valid is True

    def test_rollback_with_no_history_returns_false(self) -> None:
        migrator = self._make_migrator()
        result = migrator.rollback()
        assert result is False

    def test_rollback_unknown_id_raises_value_error(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime

        from file_organizer.methodologies.johnny_decimal.migrator import RollbackInfo

        migrator = self._make_migrator()
        # Inject a fake rollback entry
        info = RollbackInfo(
            migration_id="abc123",
            timestamp=datetime.now(UTC),
            original_structure={},
            backup_path=None,
        )
        migrator._rollback_history.append(info)
        with pytest.raises(ValueError, match="Migration ID not found"):
            migrator.rollback("nonexistent_id")

    def test_rollback_latest_restores_renames(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime

        from file_organizer.methodologies.johnny_decimal.migrator import RollbackInfo

        migrator = self._make_migrator()

        # Create a folder, "rename" it, then rollback
        original = tmp_path / "OldName"
        original.mkdir()
        renamed = tmp_path / "NewName"
        original.rename(renamed)
        assert renamed.exists()
        assert not original.exists()

        info = RollbackInfo(
            migration_id="rb001",
            timestamp=datetime.now(UTC),
            original_structure={
                str(original): (str(renamed), "OldName"),
            },
            backup_path=None,
        )
        migrator._rollback_history.append(info)
        result = migrator.rollback()
        assert result is True
        assert original.exists()
        assert not renamed.exists()

    def test_execute_migration_skips_when_target_exists(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from file_organizer.methodologies.johnny_decimal.transformer import (
            TransformationPlan,
            TransformationRule,
        )

        migrator = self._make_migrator()
        source = tmp_path / "Docs"
        source.mkdir()
        target = tmp_path / "11 Docs"
        target.mkdir()  # target already exists

        jd_num = JohnnyDecimalNumber(area=10, category=11)
        rule = TransformationRule(
            source_path=source,
            target_name="11 Docs",
            jd_number=jd_num,
            action="rename",
            confidence=0.9,
        )
        plan = TransformationPlan(root_path=tmp_path, rules=[rule], estimated_changes=1)
        result = migrator.execute_migration(plan, dry_run=False, create_backup=False)
        assert result.skipped_count == 1
        assert result.transformed_count == 0

    def test_create_migration_plan_with_real_directory(self, tmp_path: Path) -> None:
        """Integration test: scan + transform against real filesystem."""
        migrator = self._make_migrator()

        root = tmp_path / "structure"
        root.mkdir()
        (root / "Work").mkdir()
        (root / "Personal").mkdir()
        (root / "Work" / "notes.txt").write_text("work notes", encoding="utf-8")

        plan, scan_result = migrator.create_migration_plan(root)
        assert scan_result.total_folders >= 2
        assert scan_result.total_files >= 1
        assert scan_result.root_path == root
