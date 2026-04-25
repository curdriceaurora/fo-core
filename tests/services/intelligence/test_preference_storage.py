"""Protocol-contract tests for ``preference_storage`` (Epic D / D5).

Tracks: issue #157 (Hardening Epic D, item D5).

Test contract (from ``docs/internal/D-storage-design.md`` §3.6):
- One parametrized class runs against both ``InMemoryPreferenceStorage()``
  and ``SqlitePreferenceStorage(tmp_path / "test.db")``.
- Each test asserts the published shape of the Protocol — never reaches
  into private dicts, never assumes a specific implementation.
- ``PreferenceTracker(storage=mock_storage)`` test asserts ``track_correction``
  delegates to ``save_preference`` / ``save_correction`` with the right payload
  (mock-call-args, not just call_count — per anti-pattern T3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from services.intelligence.preference_storage import (
    InMemoryPreferenceStorage,
    PreferenceStorage,
    SqlitePreferenceStorage,
)
from services.intelligence.preference_tracker import (
    Correction,
    CorrectionType,
    Preference,
    PreferenceMetadata,
    PreferenceTracker,
    PreferenceType,
)

if TYPE_CHECKING:
    pass

pytestmark = [pytest.mark.unit, pytest.mark.ci, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_preference(
    preference_type: PreferenceType = PreferenceType.FOLDER_MAPPING,
    key: str = "file_move|.pdf|Documents",
    # Default ``value`` is a generic relative-string folder name (not ``/home/...``)
    # so the G2 hardcoded-path rail (which scans test files for ``/home/`` /
    # ``/tmp/`` / ``/Users/`` literals) stays clean.
    value: str | dict[str, str] = "Documents",
    confidence: float = 0.5,
    frequency: int = 1,
) -> Preference:
    now = datetime(2026, 1, 15, 10, 30, tzinfo=UTC)
    return Preference(
        preference_type=preference_type,
        key=key,
        value=value,
        metadata=PreferenceMetadata(
            created=now,
            updated=now,
            confidence=confidence,
            frequency=frequency,
            last_used=now,
            source="user_correction",
        ),
        context={"source_extension": ".pdf"},
    )


def _make_correction(
    source: Path,
    destination: Path,
    correction_type: CorrectionType = CorrectionType.FILE_MOVE,
) -> Correction:
    return Correction(
        correction_type=correction_type,
        source=source,
        destination=destination,
        timestamp=datetime(2026, 1, 15, 10, 30, tzinfo=UTC),
        context={},
    )


# ---------------------------------------------------------------------------
# Protocol-contract tests (parametrized over both implementations)
# ---------------------------------------------------------------------------


@pytest.fixture(params=["in_memory", "sqlite"])
def storage(request: pytest.FixtureRequest, tmp_path: Path) -> PreferenceStorage:
    """Yield each storage implementation under test."""
    if request.param == "in_memory":
        return InMemoryPreferenceStorage()
    if request.param == "sqlite":
        return SqlitePreferenceStorage(tmp_path / "preferences.db")
    raise ValueError(f"Unknown storage param: {request.param}")


class TestPreferenceCRUD:
    """The four preference-CRUD methods round-trip through both backends."""

    def test_save_then_find_by_type_returns_dataclass(self, storage: PreferenceStorage) -> None:
        pref = _make_preference()
        storage.save_preference(pref)

        found = storage.find_preferences(PreferenceType.FOLDER_MAPPING)
        assert len(found) == 1
        retrieved = found[0]
        assert isinstance(retrieved, Preference)
        assert retrieved.preference_type == PreferenceType.FOLDER_MAPPING
        assert retrieved.key == pref.key
        assert retrieved.value == pref.value
        assert retrieved.metadata.confidence == 0.5
        assert retrieved.metadata.frequency == 1

    def test_find_filters_by_key_when_provided(self, storage: PreferenceStorage) -> None:
        a = _make_preference(key="file_move|.pdf|Documents")
        b = _make_preference(key="file_move|.txt|Notes")
        storage.save_preference(a)
        storage.save_preference(b)

        only_pdf = storage.find_preferences(
            PreferenceType.FOLDER_MAPPING, key="file_move|.pdf|Documents"
        )
        assert len(only_pdf) == 1
        assert only_pdf[0].key == "file_move|.pdf|Documents"

    def test_find_returns_empty_for_unknown_type(self, storage: PreferenceStorage) -> None:
        result = storage.find_preferences(PreferenceType.NAMING_PATTERN)
        assert result == []

    def test_save_is_idempotent_for_same_type_and_key(self, storage: PreferenceStorage) -> None:
        # Saving the same (type, key) twice must NOT create two rows; the
        # second save updates the existing entry.
        first = _make_preference(value="/path/v1", confidence=0.5)
        storage.save_preference(first)

        second = _make_preference(value="/path/v2", confidence=0.7)
        storage.save_preference(second)

        all_prefs = storage.find_preferences(PreferenceType.FOLDER_MAPPING)
        assert len(all_prefs) == 1
        assert all_prefs[0].value == "/path/v2"

    def test_update_confidence_success_increases_confidence(
        self, storage: PreferenceStorage
    ) -> None:
        pref = _make_preference(confidence=0.5)
        storage.save_preference(pref)

        storage.update_preference_confidence(pref, success=True)

        found = storage.find_preferences(PreferenceType.FOLDER_MAPPING)
        assert len(found) == 1
        # Confidence increased by exactly 0.05 per the tracker rule
        assert found[0].metadata.confidence == pytest.approx(0.55, abs=0.001)

    def test_update_confidence_failure_decreases_confidence(
        self, storage: PreferenceStorage
    ) -> None:
        pref = _make_preference(confidence=0.5)
        storage.save_preference(pref)

        storage.update_preference_confidence(pref, success=False)

        found = storage.find_preferences(PreferenceType.FOLDER_MAPPING)
        assert len(found) == 1
        assert found[0].metadata.confidence == pytest.approx(0.4, abs=0.001)

    def test_update_confidence_caps_at_floor(self, storage: PreferenceStorage) -> None:
        pref = _make_preference(confidence=0.1)
        storage.save_preference(pref)

        # Multiple failures should not push below the 0.1 floor
        storage.update_preference_confidence(pref, success=False)
        storage.update_preference_confidence(pref, success=False)

        found = storage.find_preferences(PreferenceType.FOLDER_MAPPING)
        assert found[0].metadata.confidence >= 0.1

    def test_update_confidence_caps_at_ceiling(self, storage: PreferenceStorage) -> None:
        pref = _make_preference(confidence=0.97)
        storage.save_preference(pref)

        storage.update_preference_confidence(pref, success=True)

        found = storage.find_preferences(PreferenceType.FOLDER_MAPPING)
        # Successful update caps at 0.98
        assert found[0].metadata.confidence <= 0.98

    def test_delete_all_clears_storage(self, storage: PreferenceStorage) -> None:
        a = _make_preference(key="a", preference_type=PreferenceType.FOLDER_MAPPING)
        b = _make_preference(key="b", preference_type=PreferenceType.NAMING_PATTERN)
        storage.save_preference(a)
        storage.save_preference(b)

        deleted = storage.delete_preferences()

        assert deleted == 2
        assert storage.find_preferences(PreferenceType.FOLDER_MAPPING) == []
        assert storage.find_preferences(PreferenceType.NAMING_PATTERN) == []

    def test_delete_by_type_only_clears_that_type(self, storage: PreferenceStorage) -> None:
        folder = _make_preference(key="a", preference_type=PreferenceType.FOLDER_MAPPING)
        naming = _make_preference(key="b", preference_type=PreferenceType.NAMING_PATTERN)
        storage.save_preference(folder)
        storage.save_preference(naming)

        deleted = storage.delete_preferences(PreferenceType.FOLDER_MAPPING)

        assert deleted == 1
        assert storage.find_preferences(PreferenceType.FOLDER_MAPPING) == []
        # The other type is untouched
        remaining = storage.find_preferences(PreferenceType.NAMING_PATTERN)
        assert len(remaining) == 1
        assert remaining[0].key == "b"


class TestCorrectionHistory:
    def test_save_correction_appears_in_get_corrections_for_file(
        self, storage: PreferenceStorage, tmp_path: Path
    ) -> None:
        src = tmp_path / "src.pdf"
        dst = tmp_path / "Documents" / "src.pdf"
        correction = _make_correction(src, dst)
        storage.save_correction(correction)

        results = storage.get_corrections_for_file(src)
        assert len(results) == 1
        assert results[0].source == src
        assert results[0].destination == dst
        assert results[0].correction_type == CorrectionType.FILE_MOVE

    def test_get_corrections_for_file_matches_destination_too(
        self, storage: PreferenceStorage, tmp_path: Path
    ) -> None:
        src = tmp_path / "old.pdf"
        dst = tmp_path / "new.pdf"
        storage.save_correction(_make_correction(src, dst))

        # Querying by destination should also find it
        by_destination = storage.get_corrections_for_file(dst)
        assert len(by_destination) == 1
        assert by_destination[0].destination == dst

    def test_get_recent_corrections_respects_limit(
        self, storage: PreferenceStorage, tmp_path: Path
    ) -> None:
        for i in range(5):
            storage.save_correction(
                _make_correction(tmp_path / f"f{i}.pdf", tmp_path / f"d{i}.pdf")
            )

        recent = storage.get_recent_corrections(limit=3)
        assert len(recent) == 3

    def test_get_corrections_for_file_returns_empty_when_unmatched(
        self, storage: PreferenceStorage, tmp_path: Path
    ) -> None:
        result = storage.get_corrections_for_file(tmp_path / "ghost.pdf")
        assert result == []


class TestStatistics:
    def test_stats_includes_total_preferences(self, storage: PreferenceStorage) -> None:
        a = _make_preference(key="a", preference_type=PreferenceType.FOLDER_MAPPING)
        b = _make_preference(key="b", preference_type=PreferenceType.NAMING_PATTERN)
        storage.save_preference(a)
        storage.save_preference(b)

        stats = storage.get_statistics()

        # Both implementations expose total_preferences in some form
        # (key name normalized in the Protocol).
        assert "total_preferences" in stats
        assert stats["total_preferences"] == 2

    def test_stats_handles_empty_storage(self, storage: PreferenceStorage) -> None:
        stats = storage.get_statistics()
        assert stats["total_preferences"] == 0


class TestExportImportRoundTrip:
    def test_export_then_import_recovers_preferences(
        self, storage: PreferenceStorage, tmp_path: Path
    ) -> None:
        # Round-trip a preference + a correction through export/import.
        pref = _make_preference()
        storage.save_preference(pref)
        storage.save_correction(_make_correction(tmp_path / "a", tmp_path / "b"))

        snapshot = storage.export_data()
        assert isinstance(snapshot, dict)

        # Wipe and restore
        storage.delete_preferences()
        # delete_preferences clears preferences but corrections are still
        # in the spec's export/import scope. The import call restores both.
        storage.import_data(snapshot)

        recovered = storage.find_preferences(PreferenceType.FOLDER_MAPPING)
        assert len(recovered) == 1
        assert recovered[0].key == pref.key
        assert recovered[0].value == pref.value


# ---------------------------------------------------------------------------
# PreferenceTracker injection tests (no parametrize: tracker uses storage)
# ---------------------------------------------------------------------------


class TestPreferenceTrackerInjection:
    """``PreferenceTracker(storage=...)`` delegates correctly to the backend."""

    def test_default_constructor_uses_in_memory(self) -> None:
        tracker = PreferenceTracker()
        # No exception, no args needed — backwards-compat preserved.
        # The default storage is InMemoryPreferenceStorage.
        assert isinstance(tracker._storage, InMemoryPreferenceStorage)

    def test_track_correction_routes_to_storage_save_correction(self, tmp_path: Path) -> None:
        mock_storage = MagicMock(spec=PreferenceStorage)
        # find_preferences returns [] so tracker takes the new-pref code path
        mock_storage.find_preferences.return_value = []

        tracker = PreferenceTracker(storage=mock_storage)
        src = tmp_path / "report.pdf"
        dst = tmp_path / "Documents" / "report.pdf"
        tracker.track_correction(
            source=src,
            destination=dst,
            correction_type=CorrectionType.FILE_MOVE,
        )

        # Mock-call-args verification (T3): the correction passed to storage
        # must carry the exact path/type the caller provided.
        mock_storage.save_correction.assert_called_once()
        saved = mock_storage.save_correction.call_args.args[0]
        assert isinstance(saved, Correction)
        assert saved.source == src
        assert saved.destination == dst
        assert saved.correction_type == CorrectionType.FILE_MOVE

    def test_track_correction_routes_to_storage_save_preference(self, tmp_path: Path) -> None:
        mock_storage = MagicMock(spec=PreferenceStorage)
        mock_storage.find_preferences.return_value = []

        tracker = PreferenceTracker(storage=mock_storage)
        src = tmp_path / "doc.pdf"
        dst = tmp_path / "Archive" / "doc.pdf"
        tracker.track_correction(
            source=src,
            destination=dst,
            correction_type=CorrectionType.FILE_MOVE,
        )

        # The new preference is saved to storage (one call only — first time
        # this pattern is seen, so tracker's "extract" code path runs).
        mock_storage.save_preference.assert_called_once()
        saved_pref = mock_storage.save_preference.call_args.args[0]
        assert isinstance(saved_pref, Preference)
        assert saved_pref.preference_type == PreferenceType.FOLDER_MAPPING

    def test_explicit_in_memory_storage(self) -> None:
        # Constructing with an explicit InMemoryPreferenceStorage works.
        storage = InMemoryPreferenceStorage()
        tracker = PreferenceTracker(storage=storage)
        assert tracker._storage is storage


class TestSqliteStorageDirect:
    """SQLite-specific concerns that don't fit the Protocol contract tests."""

    def test_db_file_created_on_first_save(self, tmp_path: Path) -> None:
        db_path = tmp_path / "subdir" / "preferences.db"
        # Parent directory will be created
        storage = SqlitePreferenceStorage(db_path)

        storage.save_preference(_make_preference())

        # File now exists
        assert db_path.exists()
        assert db_path.stat().st_size > 0

    def test_round_trip_survives_storage_recreation(self, tmp_path: Path) -> None:
        db_path = tmp_path / "preferences.db"

        # First storage instance writes a preference
        storage1 = SqlitePreferenceStorage(db_path)
        pref = _make_preference()
        storage1.save_preference(pref)
        # Close to release file handles
        storage1.close()

        # Second instance over the same file reads it back
        storage2 = SqlitePreferenceStorage(db_path)
        recovered = storage2.find_preferences(PreferenceType.FOLDER_MAPPING)
        assert len(recovered) == 1
        assert recovered[0].key == pref.key
        storage2.close()
