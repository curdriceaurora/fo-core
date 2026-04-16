"""Integration tests for pipeline orchestrator and related services.

Covers:
  - pipeline/orchestrator.py       — PipelineOrchestrator (all major paths)
  - services/intelligence/preference_store.py — PreferenceStore
  - services/copilot/executor.py   — CommandExecutor
  - services/intelligence/profile_migrator.py — ProfileMigrator
  - services/deduplication/embedder.py — DocumentEmbedder
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# PipelineOrchestrator
# ---------------------------------------------------------------------------


class TestPipelineOrchestratorInit:
    def test_default_init(self) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        orch = PipelineOrchestrator()
        assert orch.config is not None
        assert orch.is_running is False

    def test_invalid_memory_pressure_threshold_raises(self) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        with pytest.raises(ValueError, match="memory_pressure_threshold_percent"):
            PipelineOrchestrator(memory_pressure_threshold_percent=101.0)

    def test_invalid_memory_pressure_below_zero_raises(self) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        with pytest.raises(ValueError, match="memory_pressure_threshold_percent"):
            PipelineOrchestrator(memory_pressure_threshold_percent=-1.0)

    def test_stages_empty_by_default(self) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        orch = PipelineOrchestrator()
        assert orch.stages == []

    def test_stages_from_constructor(self) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        stage = MagicMock()
        stage.name = "test_stage"
        orch = PipelineOrchestrator(stages=[stage])
        assert len(orch.stages) == 1

    def test_buffer_pool_lazy_init(self) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        orch = PipelineOrchestrator()
        pool = orch.buffer_pool
        assert pool is not None
        assert orch.buffer_pool is pool  # same instance on second access


class TestPipelineOrchestratorLifecycle:
    def test_start_sets_running(self) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        orch = PipelineOrchestrator()
        orch.start()
        assert orch.is_running is True
        orch.stop()

    def test_stop_clears_running(self) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        orch = PipelineOrchestrator()
        orch.start()
        orch.stop()
        assert orch.is_running is False

    def test_double_start_raises(self) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        orch = PipelineOrchestrator()
        orch.start()
        try:
            with pytest.raises(RuntimeError, match="already running"):
                orch.start()
        finally:
            orch.stop()

    def test_stop_when_not_running_is_safe(self) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        orch = PipelineOrchestrator()
        orch.stop()  # should not raise
        assert orch.is_running is False

    def test_set_stages_replaces_list(self) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        orch = PipelineOrchestrator()
        s1 = MagicMock()
        s1.name = "s1"
        s2 = MagicMock()
        s2.name = "s2"
        orch.set_stages([s1])
        assert len(orch.stages) == 1
        orch.set_stages([s1, s2])
        assert len(orch.stages) == 2


class TestPipelineOrchestratorLegacyProcessing:
    def test_process_nonexistent_file_returns_failure(self, tmp_path: Path) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        orch = PipelineOrchestrator()
        result = orch.process_file(tmp_path / "ghost.pdf")
        assert result.success is False
        assert "not found" in result.error.lower()

    def test_process_directory_returns_failure(self, tmp_path: Path) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        orch = PipelineOrchestrator()
        result = orch.process_file(tmp_path)
        assert result.success is False
        assert "not a file" in result.error.lower()

    def test_process_unsupported_extension_returns_failure(self, tmp_path: Path) -> None:
        from pipeline.config import PipelineConfig
        from pipeline.orchestrator import PipelineOrchestrator

        cfg = PipelineConfig(supported_extensions={".pdf"})
        orch = PipelineOrchestrator(config=cfg)
        f = tmp_path / "file.xyz"
        f.write_text("data")
        result = orch.process_file(f)
        assert result.success is False
        assert "unsupported" in result.error.lower()

    def test_process_unknown_processor_type_is_skipped(self, tmp_path: Path) -> None:
        from pipeline.config import PipelineConfig
        from pipeline.orchestrator import PipelineOrchestrator
        from pipeline.router import ProcessorType

        cfg = PipelineConfig(supported_extensions={".xyz"})
        orch = PipelineOrchestrator(config=cfg)

        with patch.object(orch.router, "route", return_value=ProcessorType.UNKNOWN):
            f = tmp_path / "file.xyz"
            f.write_text("data")
            result = orch.process_file(f)

        assert result.success is False
        assert "no processor" in result.error.lower()

    def test_process_processor_init_failure(self, tmp_path: Path) -> None:
        from pipeline.config import PipelineConfig
        from pipeline.orchestrator import PipelineOrchestrator
        from pipeline.router import ProcessorType

        cfg = PipelineConfig(supported_extensions={".pdf"})
        orch = PipelineOrchestrator(config=cfg)

        with (
            patch.object(orch.router, "route", return_value=ProcessorType.TEXT),
            patch.object(orch.processor_pool, "get_processor", return_value=None),
        ):
            f = tmp_path / "file.pdf"
            f.write_text("data")
            result = orch.process_file(f)

        assert result.success is False
        assert "failed to initialize" in result.error.lower()
        assert orch.stats.failed == 1

    def test_process_file_processor_exception_is_captured(self, tmp_path: Path) -> None:
        from pipeline.config import PipelineConfig
        from pipeline.orchestrator import PipelineOrchestrator
        from pipeline.router import ProcessorType

        cfg = PipelineConfig(supported_extensions={".pdf"})
        orch = PipelineOrchestrator(config=cfg)

        mock_proc = MagicMock()
        mock_proc.process_file.side_effect = RuntimeError("processing exploded")

        with (
            patch.object(orch.router, "route", return_value=ProcessorType.TEXT),
            patch.object(orch.processor_pool, "get_processor", return_value=mock_proc),
        ):
            f = tmp_path / "file.pdf"
            f.write_text("data")
            result = orch.process_file(f)

        assert result.success is False
        assert "processing exploded" in result.error
        assert orch.stats.failed == 1

    def test_successful_legacy_processing_updates_stats(self, tmp_path: Path) -> None:
        from pipeline.config import PipelineConfig
        from pipeline.orchestrator import PipelineOrchestrator
        from pipeline.router import ProcessorType

        cfg = PipelineConfig(
            supported_extensions={".pdf"},
            output_directory=tmp_path / "out",
        )
        orch = PipelineOrchestrator(config=cfg)

        # normalize_processor_result reads .folder_name / .filename attributes
        mock_result = MagicMock()
        mock_result.folder_name = "docs"
        mock_result.filename = "file"
        mock_result.error = None
        mock_proc = MagicMock()
        mock_proc.process_file.return_value = mock_result

        with (
            patch.object(orch.router, "route", return_value=ProcessorType.TEXT),
            patch.object(orch.processor_pool, "get_processor", return_value=mock_proc),
        ):
            f = tmp_path / "file.pdf"
            f.write_text("data")
            result = orch.process_file(f)

        assert result.success is True
        assert result.category == "docs"
        assert orch.stats.successful == 1
        assert orch.stats.total_processed == 1

    def test_notification_callback_called_on_success(self, tmp_path: Path) -> None:
        from pipeline.config import PipelineConfig
        from pipeline.orchestrator import PipelineOrchestrator
        from pipeline.router import ProcessorType

        notified: list[tuple[Path, bool]] = []

        def callback(path: Path, success: bool) -> None:
            notified.append((path, success))

        cfg = PipelineConfig(
            supported_extensions={".pdf"},
            output_directory=tmp_path / "out",
            notification_callback=callback,
        )
        orch = PipelineOrchestrator(config=cfg)

        mock_result = MagicMock()
        mock_result.folder_name = "docs"
        mock_result.filename = "file"
        mock_result.error = None
        mock_proc = MagicMock()
        mock_proc.process_file.return_value = mock_result

        with (
            patch.object(orch.router, "route", return_value=ProcessorType.TEXT),
            patch.object(orch.processor_pool, "get_processor", return_value=mock_proc),
        ):
            f = tmp_path / "file.pdf"
            f.write_text("data")
            orch.process_file(f)

        assert len(notified) == 1
        assert notified[0][1] is True  # success


class TestPipelineOrchestratorStagedProcessing:
    def _make_stage(self, name: str = "s") -> MagicMock:
        stage = MagicMock()
        stage.name = name
        # By default process() returns the context unchanged
        stage.process.side_effect = lambda ctx: ctx
        return stage

    def test_staged_process_calls_stage(self, tmp_path: Path) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        stage = self._make_stage("test")
        orch = PipelineOrchestrator(stages=[stage])
        f = tmp_path / "file.txt"
        f.write_text("hello")
        result = orch.process_file(f)
        assert stage.process.called
        assert result.success is True

    def test_stage_returning_none_marks_failure(self, tmp_path: Path) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        stage = self._make_stage("bad_stage")
        # side_effect takes precedence; clear it so return_value=None is used
        stage.process.side_effect = None
        stage.process.return_value = None
        orch = PipelineOrchestrator(stages=[stage])
        f = tmp_path / "file.txt"
        f.write_text("hello")
        result = orch.process_file(f)
        assert result.success is False
        assert "returned None" in result.error

    def test_stage_exception_marks_failure(self, tmp_path: Path) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        stage = self._make_stage("exploding_stage")
        stage.process.side_effect = ValueError("boom")
        orch = PipelineOrchestrator(stages=[stage])
        f = tmp_path / "file.txt"
        f.write_text("hello")
        result = orch.process_file(f)
        assert result.success is False
        assert "boom" in result.error

    def test_second_stage_skipped_after_failure(self, tmp_path: Path) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        stage1 = self._make_stage("s1")
        stage1.process.side_effect = None  # clear so return_value=None takes effect
        stage1.process.return_value = None  # causes failure
        stage2 = self._make_stage("s2")
        orch = PipelineOrchestrator(stages=[stage1, stage2])
        f = tmp_path / "file.txt"
        f.write_text("hello")
        orch.process_file(f)
        stage2.process.assert_not_called()

    def test_stats_incremented_for_staged_success(self, tmp_path: Path) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        stage = self._make_stage()
        orch = PipelineOrchestrator(stages=[stage])
        f = tmp_path / "file.txt"
        f.write_text("hello")
        orch.process_file(f)
        assert orch.stats.successful == 1
        assert orch.stats.total_processed == 1

    def test_stats_incremented_for_staged_failure(self, tmp_path: Path) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        stage = self._make_stage()
        stage.process.side_effect = None
        stage.process.return_value = None
        orch = PipelineOrchestrator(stages=[stage])
        f = tmp_path / "file.txt"
        f.write_text("hello")
        orch.process_file(f)
        assert orch.stats.failed == 1

    def test_process_batch_empty_returns_empty(self) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        orch = PipelineOrchestrator()
        assert orch.process_batch([]) == []

    def test_process_batch_sequential(self, tmp_path: Path) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        stage = self._make_stage()
        orch = PipelineOrchestrator(stages=[stage], prefetch_depth=0)
        files = []
        for i in range(3):
            f = tmp_path / f"file{i}.txt"
            f.write_text(f"content {i}")
            files.append(f)
        results = orch.process_batch(files)
        assert len(results) == 3
        assert all(r.success for r in results)

    def test_process_batch_prefetch(self, tmp_path: Path) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        stage = self._make_stage()
        orch = PipelineOrchestrator(stages=[stage], prefetch_depth=2, prefetch_stages=1)
        files = []
        for i in range(4):
            f = tmp_path / f"f{i}.txt"
            f.write_text(f"c{i}")
            files.append(f)
        results = orch.process_batch(files)
        assert len(results) == 4
        # Every file should succeed
        assert all(r.success for r in results)

    def test_organize_file_copies_and_handles_duplicate(self, tmp_path: Path) -> None:
        from pipeline.orchestrator import PipelineOrchestrator

        orch = PipelineOrchestrator()
        src = tmp_path / "src.txt"
        src.write_text("data")
        dst = tmp_path / "out" / "dst.txt"
        orch._organize_file(src, dst)
        assert dst.exists()
        # Calling again should create dst_1.txt
        orch._organize_file(src, dst)
        assert (tmp_path / "out" / "dst_1.txt").exists()


# ---------------------------------------------------------------------------
# PreferenceStore
# ---------------------------------------------------------------------------


class TestPreferenceStoreLoad:
    def test_load_from_empty_dir_uses_defaults(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path)
        result = store.load_preferences()
        assert result is False  # no file → defaults
        assert store._preferences["version"] == "1.0"
        assert isinstance(store._preferences["directory_preferences"], dict)

    def test_load_valid_file_returns_true(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path)
        prefs_data: dict[str, Any] = {
            "version": "1.0",
            "user_id": "tester",
            "global_preferences": {
                "folder_mappings": {},
                "naming_patterns": {},
                "category_overrides": {},
            },
            "directory_preferences": {},
        }
        (tmp_path / "preferences.json").write_text(json.dumps(prefs_data), encoding="utf-8")
        assert store.load_preferences() is True
        assert store._preferences["user_id"] == "tester"

    def test_load_invalid_schema_falls_back_to_backup(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        (tmp_path / "preferences.json").write_text('{"broken": true}', encoding="utf-8")
        store = PreferenceStore(storage_path=tmp_path)
        # No backup exists → defaults
        result = store.load_preferences()
        assert result is False

    def test_load_corrupted_json_falls_back_to_backup(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        (tmp_path / "preferences.json").write_text("NOT JSON {{{{", encoding="utf-8")
        store = PreferenceStore(storage_path=tmp_path)
        result = store.load_preferences()
        assert result is False  # No backup → defaults

    def test_load_backup_when_primary_invalid(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        valid: dict[str, Any] = {
            "version": "1.0",
            "user_id": "backup_user",
            "global_preferences": {
                "folder_mappings": {},
                "naming_patterns": {},
                "category_overrides": {},
            },
            "directory_preferences": {},
        }
        (tmp_path / "preferences.json").write_text('{"broken": true}', encoding="utf-8")
        (tmp_path / "preferences.json.backup").write_text(json.dumps(valid), encoding="utf-8")
        store = PreferenceStore(storage_path=tmp_path)
        result = store.load_preferences()
        assert result is True
        assert store._preferences["user_id"] == "backup_user"


class TestPreferenceStoreSave:
    def test_save_creates_file(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path)
        store.load_preferences()
        assert store.save_preferences() is True
        assert (tmp_path / "preferences.json").exists()

    def test_save_creates_backup(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path)
        store.load_preferences()
        store.save_preferences()
        store.save_preferences()  # second save creates backup from first
        assert (tmp_path / "preferences.json.backup").exists()


class TestPreferenceStoreAddGet:
    def test_add_and_get_preference(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path)
        target_dir = tmp_path / "docs"
        target_dir.mkdir()
        store.add_preference(target_dir, {"folder_mappings": {"pdf": "documents"}})
        pref = store.get_preference(target_dir, fallback_to_parent=False)
        assert pref is not None
        assert pref["folder_mappings"]["pdf"] == "documents"

    def test_get_preference_fallback_to_global(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path)
        store.load_preferences()
        unknown_dir = tmp_path / "unknown_dir"
        pref = store.get_preference(unknown_dir, fallback_to_parent=False)
        assert pref is not None
        # Should return global preferences structure
        assert "folder_mappings" in pref

    def test_add_preference_updates_existing(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path)
        target_dir = tmp_path / "docs"
        target_dir.mkdir()
        store.add_preference(target_dir, {"folder_mappings": {"pdf": "docs"}})
        store.add_preference(target_dir, {"folder_mappings": {"pdf": "updated_docs"}})
        pref = store.get_preference(target_dir, fallback_to_parent=False)
        assert pref is not None
        assert pref["folder_mappings"]["pdf"] == "updated_docs"

    def test_get_preference_parent_fallback(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path)
        store.add_preference(tmp_path, {"folder_mappings": {"doc": "parent_folder"}})
        child = tmp_path / "child_dir"
        child.mkdir()
        pref = store.get_preference(child, fallback_to_parent=True)
        assert pref is not None
        assert pref["folder_mappings"]["doc"] == "parent_folder"


class TestPreferenceStoreConfidence:
    def test_update_confidence_success_increases_score(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path)
        target = tmp_path / "dir"
        target.mkdir()
        store.add_preference(target, {"confidence": 0.5})
        store.update_confidence(target, success=True)
        path_str = str(target.resolve())
        new_conf = store._preferences["directory_preferences"][path_str]["confidence"]
        assert new_conf > 0.5

    def test_update_confidence_failure_decreases_score(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path)
        target = tmp_path / "dir"
        target.mkdir()
        store.add_preference(target, {"confidence": 0.8})
        store.update_confidence(target, success=False)
        path_str = str(target.resolve())
        new_conf = store._preferences["directory_preferences"][path_str]["confidence"]
        assert new_conf < 0.8

    def test_update_confidence_noop_for_unknown_path(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path)
        store.load_preferences()
        ghost = tmp_path / "ghost"
        store.update_confidence(ghost, success=True)  # should not raise


class TestPreferenceStoreResolveConflicts:
    def test_empty_list_returns_empty(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path)
        assert store.resolve_conflicts([]) == {}

    def test_single_item_returned_unchanged(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path)
        pref = {"confidence": 0.9, "correction_count": 5}
        result = store.resolve_conflicts([pref])
        assert result["confidence"] == 0.9

    def test_highest_scored_preference_wins(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path)
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        high = {"confidence": 0.95, "correction_count": 10, "updated": now}
        low = {"confidence": 0.1, "correction_count": 0, "updated": "2000-01-01T00:00:00Z"}
        result = store.resolve_conflicts([low, high])
        assert result["confidence"] == 0.95


class TestPreferenceStoreStats:
    def test_statistics_empty_store(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path)
        store.load_preferences()
        stats = store.get_statistics()
        assert stats["total_directories"] == 0
        assert stats["average_confidence"] == 0.0
        assert stats["schema_version"] == "1.0"

    def test_statistics_with_entries(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path)
        d1 = tmp_path / "d1"
        d1.mkdir()
        d2 = tmp_path / "d2"
        d2.mkdir()
        store.add_preference(d1, {"confidence": 0.8})
        store.add_preference(d2, {"confidence": 0.6})
        stats = store.get_statistics()
        assert stats["total_directories"] == 2
        assert 0.0 < stats["average_confidence"] < 1.0

    def test_clear_preferences_resets(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path)
        d = tmp_path / "d"
        d.mkdir()
        store.add_preference(d, {"confidence": 0.9})
        store.clear_preferences()
        stats = store.get_statistics()
        assert stats["total_directories"] == 0

    def test_list_directory_preferences(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path)
        d = tmp_path / "d"
        d.mkdir()
        store.add_preference(d, {"confidence": 0.7})
        listing = store.list_directory_preferences()
        assert len(listing) == 1
        path_str, pref = listing[0]
        assert "confidence" in pref


class TestPreferenceStoreExportImport:
    def test_export_creates_json_file(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path)
        store.load_preferences()
        export_path = tmp_path / "export.json"
        assert store.export_json(export_path) is True
        assert export_path.exists()
        data = json.loads(export_path.read_text())
        assert "version" in data

    def test_import_from_valid_file(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        valid: dict[str, Any] = {
            "version": "1.0",
            "user_id": "imported_user",
            "global_preferences": {
                "folder_mappings": {},
                "naming_patterns": {},
                "category_overrides": {},
            },
            "directory_preferences": {},
        }
        import_file = tmp_path / "import.json"
        import_file.write_text(json.dumps(valid), encoding="utf-8")

        store = PreferenceStore(storage_path=tmp_path)
        assert store.import_json(import_file) is True
        assert store._preferences["user_id"] == "imported_user"

    def test_import_missing_file_returns_false(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path)
        result = store.import_json(tmp_path / "ghost.json")
        assert result is False

    def test_import_invalid_schema_returns_false(self, tmp_path: Path) -> None:
        from services.intelligence.preference_store import PreferenceStore

        bad_file = tmp_path / "bad.json"
        bad_file.write_text('{"bad": "schema"}', encoding="utf-8")
        store = PreferenceStore(storage_path=tmp_path)
        result = store.import_json(bad_file)
        assert result is False


# ---------------------------------------------------------------------------
# CommandExecutor
# ---------------------------------------------------------------------------


class TestCommandExecutorInit:
    def test_default_working_dir(self) -> None:
        from services.copilot.executor import CommandExecutor

        exec_ = CommandExecutor()
        assert exec_._working_dir.exists()

    def test_custom_working_dir(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        exec_ = CommandExecutor(working_directory=str(tmp_path))
        assert exec_._working_dir == tmp_path


class TestCommandExecutorUnknownIntent:
    def test_unknown_intent_type_returns_failure(self) -> None:
        from services.copilot.executor import CommandExecutor
        from services.copilot.models import Intent, IntentType

        exec_ = CommandExecutor()
        intent = Intent(intent_type=IntentType.STATUS)
        result = exec_.execute(intent)
        assert result.success is False
        assert "No handler" in result.message


class TestCommandExecutorHandlerExceptionCaught:
    def test_exception_in_handler_returns_failure(self) -> None:
        from services.copilot.executor import CommandExecutor
        from services.copilot.models import Intent, IntentType

        exec_ = CommandExecutor()
        intent = Intent(intent_type=IntentType.UNDO)

        with patch(
            "services.copilot.executor.CommandExecutor._handle_undo",
            side_effect=RuntimeError("handler crashed"),
        ):
            result = exec_.execute(intent)

        assert result.success is False
        assert "handler crashed" in result.message


class TestCommandExecutorMove:
    def test_move_missing_source_param_returns_failure(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor
        from services.copilot.models import Intent, IntentType

        exec_ = CommandExecutor(working_directory=str(tmp_path))
        intent = Intent(intent_type=IntentType.MOVE, parameters={"destination": "dst.txt"})
        result = exec_.execute(intent)
        assert result.success is False
        assert "source" in result.message.lower() or "destination" in result.message.lower()

    def test_move_nonexistent_source_returns_failure(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor
        from services.copilot.models import Intent, IntentType

        exec_ = CommandExecutor(working_directory=str(tmp_path))
        intent = Intent(
            intent_type=IntentType.MOVE,
            parameters={"source": "ghost.txt", "destination": "dst.txt"},
        )
        result = exec_.execute(intent)
        assert result.success is False
        assert "not found" in result.message.lower()

    def test_move_file_success(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor
        from services.copilot.models import Intent, IntentType

        src = tmp_path / "src.txt"
        src.write_text("content")
        dst = tmp_path / "subdir" / "dst.txt"
        exec_ = CommandExecutor(working_directory=str(tmp_path))
        intent = Intent(
            intent_type=IntentType.MOVE,
            parameters={"source": str(src), "destination": str(dst)},
        )
        result = exec_.execute(intent)
        assert result.success is True
        assert dst.exists()
        assert len(result.affected_files) == 2


class TestCommandExecutorRename:
    def test_rename_missing_params_returns_failure(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor
        from services.copilot.models import Intent, IntentType

        exec_ = CommandExecutor(working_directory=str(tmp_path))
        intent = Intent(intent_type=IntentType.RENAME, parameters={"target": "only.txt"})
        result = exec_.execute(intent)
        assert result.success is False

    def test_rename_nonexistent_file_returns_failure(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor
        from services.copilot.models import Intent, IntentType

        exec_ = CommandExecutor(working_directory=str(tmp_path))
        intent = Intent(
            intent_type=IntentType.RENAME,
            parameters={"target": "ghost.txt", "new_name": "new.txt"},
        )
        result = exec_.execute(intent)
        assert result.success is False

    def test_rename_file_success(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor
        from services.copilot.models import Intent, IntentType

        src = tmp_path / "old.txt"
        src.write_text("data")
        exec_ = CommandExecutor(working_directory=str(tmp_path))
        intent = Intent(
            intent_type=IntentType.RENAME,
            parameters={"target": str(src), "new_name": "new.txt"},
        )
        result = exec_.execute(intent)
        assert result.success is True
        assert (tmp_path / "new.txt").exists()
        assert result.affected_files[1].endswith("new.txt")


class TestCommandExecutorFind:
    def test_find_empty_query_returns_failure(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor
        from services.copilot.models import Intent, IntentType

        exec_ = CommandExecutor(working_directory=str(tmp_path))
        intent = Intent(intent_type=IntentType.FIND, parameters={"query": ""})
        result = exec_.execute(intent)
        assert result.success is False

    def test_find_by_filename_match(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor
        from services.copilot.models import Intent, IntentType

        (tmp_path / "report_2024.pdf").write_text("data")
        (tmp_path / "notes.txt").write_text("notes")
        exec_ = CommandExecutor(working_directory=str(tmp_path))
        intent = Intent(
            intent_type=IntentType.FIND,
            parameters={"query": "report", "paths": [str(tmp_path)]},
        )
        result = exec_.execute(intent)
        assert result.success is True
        assert len(result.affected_files) >= 1
        assert any("report" in f for f in result.affected_files)

    def test_find_no_match_returns_success_empty(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor
        from services.copilot.models import Intent, IntentType

        exec_ = CommandExecutor(working_directory=str(tmp_path))
        intent = Intent(
            intent_type=IntentType.FIND,
            parameters={"query": "zzz_no_match_xyz", "paths": [str(tmp_path)]},
        )
        result = exec_.execute(intent)
        assert result.success is True
        assert result.affected_files == []


class TestCommandExecutorPreview:
    def test_preview_on_nonexistent_dir_returns_failure(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor
        from services.copilot.models import Intent, IntentType

        exec_ = CommandExecutor(working_directory=str(tmp_path))
        intent = Intent(
            intent_type=IntentType.PREVIEW,
            parameters={"source": str(tmp_path / "ghost_dir")},
        )
        result = exec_.execute(intent)
        assert result.success is False


class TestCommandExecutorSuggest:
    def test_suggest_empty_paths_returns_failure(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor
        from services.copilot.models import Intent, IntentType

        exec_ = CommandExecutor(working_directory=str(tmp_path))
        intent = Intent(intent_type=IntentType.SUGGEST, parameters={"paths": []})
        result = exec_.execute(intent)
        assert result.success is False
        assert "specify" in result.message.lower()

    def test_suggest_nonexistent_path_returns_failure(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor
        from services.copilot.models import Intent, IntentType

        exec_ = CommandExecutor(working_directory=str(tmp_path))
        intent = Intent(
            intent_type=IntentType.SUGGEST,
            parameters={"paths": [str(tmp_path / "ghost")]},
        )
        result = exec_.execute(intent)
        assert result.success is False

    def test_suggest_valid_path_returns_success(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor
        from services.copilot.models import Intent, IntentType

        exec_ = CommandExecutor(working_directory=str(tmp_path))
        intent = Intent(
            intent_type=IntentType.SUGGEST,
            parameters={"paths": [str(tmp_path)]},
        )
        result = exec_.execute(intent)
        assert result.success is True


class TestCommandExecutorResolvePathHelper:
    def test_resolve_none_returns_working_dir(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        exec_ = CommandExecutor(working_directory=str(tmp_path))
        resolved = exec_._resolve_path(None)
        assert resolved == tmp_path

    def test_resolve_absolute_path(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        exec_ = CommandExecutor(working_directory=str(tmp_path))
        resolved = exec_._resolve_path(str(tmp_path / "sub"))
        assert resolved == (tmp_path / "sub").resolve()

    def test_resolve_relative_joins_working_dir(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        exec_ = CommandExecutor(working_directory=str(tmp_path))
        resolved = exec_._resolve_path("relative/path")
        assert str(resolved).startswith(str(tmp_path))


# ---------------------------------------------------------------------------
# ProfileMigrator
# ---------------------------------------------------------------------------


def _make_profile_manager(storage_path: Path) -> Any:
    """Helper: create a ProfileManager with a temp storage path."""
    from services.intelligence.profile_manager import ProfileManager

    return ProfileManager(storage_path=storage_path)


def _create_profile(manager: Any, name: str = "test_profile") -> Any:
    """Helper: create and return a Profile via the manager."""
    manager.create_profile(name, description="Test profile for migration")
    return manager.get_profile(name)


class TestProfileMigratorValidateMigration:
    def test_validate_valid_profile(self, tmp_path: Path) -> None:
        from services.intelligence.profile_migrator import ProfileMigrator

        mgr = _make_profile_manager(tmp_path)
        _create_profile(mgr, "ptest")
        migrator = ProfileMigrator(profile_manager=mgr)
        assert migrator.validate_migration("ptest") is True

    def test_validate_nonexistent_profile_returns_false(self, tmp_path: Path) -> None:
        from services.intelligence.profile_migrator import ProfileMigrator

        mgr = _make_profile_manager(tmp_path)
        migrator = ProfileMigrator(profile_manager=mgr)
        assert migrator.validate_migration("ghost_profile") is False


class TestProfileMigratorMigrateVersion:
    def test_migrate_to_current_version_no_op(self, tmp_path: Path) -> None:
        from services.intelligence.profile_migrator import ProfileMigrator

        mgr = _make_profile_manager(tmp_path)
        _create_profile(mgr, "mtest")
        migrator = ProfileMigrator(profile_manager=mgr)
        # Profile is already at 1.0 → should succeed (no migration needed)
        assert migrator.migrate_version("mtest", "1.0") is True

    def test_migrate_nonexistent_profile_returns_false(self, tmp_path: Path) -> None:
        from services.intelligence.profile_migrator import ProfileMigrator

        mgr = _make_profile_manager(tmp_path)
        migrator = ProfileMigrator(profile_manager=mgr)
        assert migrator.migrate_version("ghost", "1.0") is False

    def test_migrate_unsupported_version_returns_false(self, tmp_path: Path) -> None:
        from services.intelligence.profile_migrator import ProfileMigrator

        mgr = _make_profile_manager(tmp_path)
        _create_profile(mgr, "mtest2")
        migrator = ProfileMigrator(profile_manager=mgr)
        assert migrator.migrate_version("mtest2", "99.0") is False

    def test_migrate_no_path_available_returns_false(self, tmp_path: Path) -> None:
        """Simulate a profile at a different version with no migration path registered."""
        from services.intelligence.profile_migrator import ProfileMigrator

        mgr = _make_profile_manager(tmp_path)
        _create_profile(mgr, "mtest3")
        # Force the profile to report version 0.5 (different from target 1.0)
        profile_file = tmp_path / "mtest3.json"
        data = mgr.get_profile("mtest3").to_dict()
        data["profile_version"] = "0.5"
        profile_file.write_text(json.dumps(data), encoding="utf-8")

        migrator = ProfileMigrator(profile_manager=mgr)
        # 0.5 -> 1.0 has no migration path, backup creation will fail or path not found
        result = migrator.migrate_version("mtest3", "1.0", backup=False)
        assert result is False


class TestProfileMigratorBackup:
    def test_backup_before_migration_creates_file(self, tmp_path: Path) -> None:
        from services.intelligence.profile_migrator import ProfileMigrator

        mgr = _make_profile_manager(tmp_path)
        profile = _create_profile(mgr, "btest")
        migrator = ProfileMigrator(profile_manager=mgr)
        backup_path = migrator.backup_before_migration(profile)
        assert backup_path is not None
        assert backup_path.exists()
        data = json.loads(backup_path.read_text())
        assert data["profile_name"] == "btest"

    def test_list_backups_empty_when_none_created(self, tmp_path: Path) -> None:
        from services.intelligence.profile_migrator import ProfileMigrator

        mgr = _make_profile_manager(tmp_path)
        migrator = ProfileMigrator(profile_manager=mgr)
        backups = migrator.list_backups()
        assert backups == []

    def test_list_backups_after_creation(self, tmp_path: Path) -> None:
        from services.intelligence.profile_migrator import ProfileMigrator

        mgr = _make_profile_manager(tmp_path)
        profile = _create_profile(mgr, "ltest")
        migrator = ProfileMigrator(profile_manager=mgr)
        migrator.backup_before_migration(profile)
        backups = migrator.list_backups()
        assert len(backups) == 1


class TestProfileMigratorRollback:
    def test_rollback_restores_profile(self, tmp_path: Path) -> None:
        from services.intelligence.profile_migrator import ProfileMigrator

        mgr = _make_profile_manager(tmp_path)
        profile = _create_profile(mgr, "rtest")
        migrator = ProfileMigrator(profile_manager=mgr)
        backup_path = migrator.backup_before_migration(profile)
        assert backup_path is not None
        result = migrator.rollback_migration("rtest", backup_path)
        assert result is True

    def test_rollback_missing_backup_returns_false(self, tmp_path: Path) -> None:
        from services.intelligence.profile_migrator import ProfileMigrator

        mgr = _make_profile_manager(tmp_path)
        _create_profile(mgr, "r2test")
        migrator = ProfileMigrator(profile_manager=mgr)
        result = migrator.rollback_migration("r2test", tmp_path / "nonexistent.json")
        assert result is False


class TestProfileMigratorHistory:
    def test_migration_history_empty_for_new_profile(self, tmp_path: Path) -> None:
        from services.intelligence.profile_migrator import ProfileMigrator

        mgr = _make_profile_manager(tmp_path)
        _create_profile(mgr, "htest")
        migrator = ProfileMigrator(profile_manager=mgr)
        history = migrator.get_migration_history("htest")
        assert history == []

    def test_migration_history_none_for_missing_profile(self, tmp_path: Path) -> None:
        from services.intelligence.profile_migrator import ProfileMigrator

        mgr = _make_profile_manager(tmp_path)
        migrator = ProfileMigrator(profile_manager=mgr)
        assert migrator.get_migration_history("ghost") is None

    def test_register_migration_stores_function(self, tmp_path: Path) -> None:
        from services.intelligence.profile_migrator import ProfileMigrator

        mgr = _make_profile_manager(tmp_path)
        migrator = ProfileMigrator(profile_manager=mgr)
        fn = lambda d: d  # noqa: E731
        migrator.register_migration("1.0", "2.0", fn)
        assert "1.0->2.0" in migrator._migration_functions


# ---------------------------------------------------------------------------
# DocumentEmbedder
# ---------------------------------------------------------------------------


class TestDocumentEmbedderInit:
    @pytest.fixture(autouse=True)
    def _require_sklearn(self) -> None:
        pytest.importorskip("sklearn.feature_extraction.text")

    def test_init_defaults(self) -> None:
        from services.deduplication.embedder import DocumentEmbedder

        emb = DocumentEmbedder()
        assert emb.max_features == 5000
        assert emb.is_fitted is False

    def test_init_custom_params(self) -> None:
        from services.deduplication.embedder import DocumentEmbedder

        emb = DocumentEmbedder(max_features=100, ngram_range=(1, 1))
        assert emb.max_features == 100
        assert emb.ngram_range == (1, 1)

    def test_not_fitted_raises_on_transform(self) -> None:
        from services.deduplication.embedder import DocumentEmbedder

        emb = DocumentEmbedder()
        with pytest.raises(RuntimeError, match="not fitted"):
            emb.transform("hello world")

    def test_not_fitted_raises_on_transform_batch(self) -> None:
        from services.deduplication.embedder import DocumentEmbedder

        emb = DocumentEmbedder()
        with pytest.raises(RuntimeError, match="not fitted"):
            emb.transform_batch(["hello", "world"])


class TestDocumentEmbedderFitTransform:
    @pytest.fixture(autouse=True)
    def _require_sklearn(self) -> None:
        pytest.importorskip("sklearn.feature_extraction.text")

    def test_fit_transform_returns_array(self) -> None:
        import numpy as np

        from services.deduplication.embedder import DocumentEmbedder

        emb = DocumentEmbedder(max_features=50)
        docs = ["hello world foo", "bar baz qux", "python testing code"]
        result = emb.fit_transform(docs)
        assert isinstance(result, np.ndarray)
        assert result.shape[0] == 3
        assert emb.is_fitted is True

    def test_fit_transform_empty_returns_empty(self) -> None:

        from services.deduplication.embedder import DocumentEmbedder

        emb = DocumentEmbedder()
        result = emb.fit_transform([])
        assert len(result) == 0
        assert emb.is_fitted is False

    def test_fit_transform_small_corpus_max_df_adjusted(self) -> None:
        import numpy as np

        from services.deduplication.embedder import DocumentEmbedder

        # Single doc — max_df as fraction rounds to 0; embedder adjusts temporarily
        emb = DocumentEmbedder(max_features=10, max_df=0.5)
        result = emb.fit_transform(["single document text here"])
        assert isinstance(result, np.ndarray)
        # max_df should be restored to original after fit_transform
        assert emb.vectorizer.max_df == 0.5

    def test_transform_after_fit(self) -> None:
        import numpy as np

        from services.deduplication.embedder import DocumentEmbedder

        emb = DocumentEmbedder(max_features=50)
        docs = ["alpha beta gamma", "delta epsilon zeta", "theta iota kappa"]
        emb.fit_transform(docs)
        embedding = emb.transform("alpha beta")
        assert isinstance(embedding, np.ndarray)
        assert embedding.shape[0] > 0

    def test_transform_uses_cache(self) -> None:
        from services.deduplication.embedder import DocumentEmbedder

        emb = DocumentEmbedder(max_features=50)
        docs = ["cache test doc one", "cache test doc two", "cache test three"]
        emb.fit_transform(docs)
        emb.transform("cache test doc one")
        # Second call should hit cache (no error)
        emb.transform("cache test doc one")
        assert len(emb.embedding_cache) == 1

    def test_transform_batch_returns_matrix(self) -> None:
        import numpy as np

        from services.deduplication.embedder import DocumentEmbedder

        emb = DocumentEmbedder(max_features=50)
        docs = ["first doc alpha", "second doc beta", "third doc gamma"]
        emb.fit_transform(docs)
        batch = ["first doc alpha", "second doc beta"]
        result = emb.transform_batch(batch)
        assert isinstance(result, np.ndarray)
        assert result.shape[0] == 2


class TestDocumentEmbedderVocabulary:
    @pytest.fixture(autouse=True)
    def _require_sklearn(self) -> None:
        pytest.importorskip("sklearn.feature_extraction.text")

    def test_get_feature_names_after_fit(self) -> None:
        from services.deduplication.embedder import DocumentEmbedder

        emb = DocumentEmbedder(max_features=50)
        docs = ["machine learning", "deep learning neural", "data science ml"]
        emb.fit_transform(docs)
        names = emb.get_feature_names()
        assert isinstance(names, list)
        assert len(names) > 0
        assert all(isinstance(n, str) for n in names)

    def test_get_vocabulary_after_fit(self) -> None:
        from services.deduplication.embedder import DocumentEmbedder

        emb = DocumentEmbedder(max_features=50)
        docs = ["vocabulary test one", "vocabulary test two", "vocabulary three"]
        emb.fit_transform(docs)
        vocab = emb.get_vocabulary()
        assert isinstance(vocab, dict)
        assert len(vocab) > 0

    def test_get_feature_names_before_fit_raises(self) -> None:
        from services.deduplication.embedder import DocumentEmbedder

        emb = DocumentEmbedder()
        with pytest.raises(RuntimeError, match="not fitted"):
            emb.get_feature_names()

    def test_get_top_terms(self) -> None:

        from services.deduplication.embedder import DocumentEmbedder

        emb = DocumentEmbedder(max_features=50)
        docs = ["python programming language", "java programming code", "python java scripts"]
        emb.fit_transform(docs)
        vec = emb.transform("python programming")
        top_terms = emb.get_top_terms(vec, top_n=3)
        assert isinstance(top_terms, list)
        assert all(isinstance(t, tuple) and len(t) == 2 for t in top_terms)
        # Weights should be positive
        assert all(w > 0 for _, w in top_terms)


class TestDocumentEmbedderPersistence:
    @pytest.fixture(autouse=True)
    def _require_sklearn(self) -> None:
        pytest.importorskip("sklearn.feature_extraction.text")

    def test_save_and_load_model(self, tmp_path: Path) -> None:
        import numpy as np

        from services.deduplication.embedder import DocumentEmbedder

        emb = DocumentEmbedder(max_features=50)
        docs = ["save load test alpha", "save load test beta", "save load gamma"]
        emb.fit_transform(docs)
        model_path = tmp_path / "model.pkl"
        emb.save_model(model_path)
        assert model_path.exists()

        # Load into fresh embedder
        emb2 = DocumentEmbedder(max_features=50)
        emb2.load_model(model_path)
        assert emb2.is_fitted is True
        result = emb2.transform("save load test alpha")
        assert isinstance(result, np.ndarray)

    def test_save_model_unfitted_is_noop(self, tmp_path: Path) -> None:
        from services.deduplication.embedder import DocumentEmbedder

        emb = DocumentEmbedder()
        model_path = tmp_path / "model.pkl"
        emb.save_model(model_path)  # should not raise, just logs warning
        assert not model_path.exists()

    def test_load_model_missing_file_raises(self, tmp_path: Path) -> None:
        from services.deduplication.embedder import DocumentEmbedder

        emb = DocumentEmbedder()
        with pytest.raises((OSError, Exception)):
            emb.load_model(tmp_path / "nonexistent.pkl")

    def test_clear_cache(self) -> None:
        from services.deduplication.embedder import DocumentEmbedder

        emb = DocumentEmbedder(max_features=50)
        docs = ["clear cache test one", "clear cache test two", "clear cache three"]
        emb.fit_transform(docs)
        emb.transform("clear cache test one")
        assert len(emb.embedding_cache) == 1
        emb.clear_cache()
        assert len(emb.embedding_cache) == 0

    def test_cache_path_loaded_on_init(self, tmp_path: Path) -> None:

        from services.deduplication.embedder import DocumentEmbedder

        cache_path = tmp_path / "cache.pkl"
        emb = DocumentEmbedder(max_features=50, cache_path=cache_path)
        docs = ["cache path test a", "cache path test b", "cache path test c"]
        emb.fit_transform(docs)
        emb.transform("cache path test a")
        emb._save_cache()
        assert cache_path.exists()

        emb2 = DocumentEmbedder(max_features=50, cache_path=cache_path)
        # Cache should load pre-existing entries
        assert len(emb2.embedding_cache) >= 1
