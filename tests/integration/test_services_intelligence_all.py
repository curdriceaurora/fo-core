"""Integration tests for intelligence modules.

Covers:
  - services/intelligence/confidence.py — ConfidenceEngine, PatternUsageData
  - services/intelligence/scoring.py — PatternScorer, ScoredPattern
  - services/intelligence/preference_store.py — PreferenceStore
  - services/intelligence/profile_manager.py — ProfileManager, Profile
  - services/intelligence/profile_migrator.py — ProfileMigrator
  - services/intelligence/profile_merger.py — ProfileMerger, MergeStrategy
  - services/intelligence/profile_exporter.py — ProfileExporter
  - services/intelligence/folder_learner.py — FolderPreferenceLearner
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# ConfidenceEngine
# ---------------------------------------------------------------------------


class TestConfidenceEngine:
    def test_calculate_confidence_no_data_returns_min(self) -> None:
        from file_organizer.services.intelligence.confidence import ConfidenceEngine

        engine = ConfidenceEngine()
        score = engine.calculate_confidence("unknown_pattern")
        assert score == 0.0

    def test_calculate_confidence_with_usage_data(self) -> None:
        from file_organizer.services.intelligence.confidence import (
            ConfidenceEngine,
            PatternUsageData,
        )

        engine = ConfidenceEngine()
        data = PatternUsageData(pattern_id="p1")
        now = datetime.now(UTC)
        for _ in range(10):
            data.add_usage(now, success=True)

        score = engine.calculate_confidence("p1", usage_data=data, current_time=now)
        assert 0.0 < score <= 1.0

    def test_track_usage_and_calculate(self) -> None:
        from file_organizer.services.intelligence.confidence import ConfidenceEngine

        engine = ConfidenceEngine()
        now = datetime.now(UTC)
        for _ in range(5):
            engine.track_usage("pat_a", now, success=True)

        score = engine.calculate_confidence("pat_a", current_time=now)
        assert score > 0.0

    def test_get_confidence_level_high(self) -> None:
        from file_organizer.services.intelligence.confidence import ConfidenceEngine

        engine = ConfidenceEngine()
        assert engine.get_confidence_level(0.9) == "high"

    def test_get_confidence_level_medium(self) -> None:
        from file_organizer.services.intelligence.confidence import ConfidenceEngine

        engine = ConfidenceEngine()
        assert engine.get_confidence_level(0.6) == "medium"

    def test_get_confidence_level_low(self) -> None:
        from file_organizer.services.intelligence.confidence import ConfidenceEngine

        engine = ConfidenceEngine()
        assert engine.get_confidence_level(0.3) == "low"

    def test_get_confidence_level_very_low(self) -> None:
        from file_organizer.services.intelligence.confidence import ConfidenceEngine

        engine = ConfidenceEngine()
        assert engine.get_confidence_level(0.1) == "very_low"

    def test_validate_confidence_threshold_pass(self) -> None:
        from file_organizer.services.intelligence.confidence import ConfidenceEngine

        engine = ConfidenceEngine()
        assert engine.validate_confidence_threshold(0.8, 0.75) is True

    def test_validate_confidence_threshold_fail(self) -> None:
        from file_organizer.services.intelligence.confidence import ConfidenceEngine

        engine = ConfidenceEngine()
        assert engine.validate_confidence_threshold(0.5, 0.75) is False

    def test_decay_old_patterns(self) -> None:
        from file_organizer.services.intelligence.confidence import ConfidenceEngine

        engine = ConfidenceEngine(decay_half_life_days=30, old_pattern_threshold_days=90)
        old_date = (datetime.now(UTC) - timedelta(days=200)).isoformat()
        patterns = [{"id": "old", "confidence": 0.8, "last_used": old_date}]
        decayed = engine.decay_old_patterns(patterns)
        assert decayed[0]["confidence"] < 0.8
        assert decayed[0].get("decayed") is True

    def test_decay_skips_pattern_without_last_used(self) -> None:
        from file_organizer.services.intelligence.confidence import ConfidenceEngine

        engine = ConfidenceEngine()
        patterns = [{"id": "no_date", "confidence": 0.7}]
        result = engine.decay_old_patterns(patterns)
        assert result[0]["confidence"] == 0.7

    def test_boost_recent_patterns(self) -> None:
        from file_organizer.services.intelligence.confidence import ConfidenceEngine

        engine = ConfidenceEngine()
        now = datetime.now(UTC)
        recent_date = (now - timedelta(days=2)).isoformat()
        patterns = [{"id": "recent", "confidence": 0.6, "last_used": recent_date}]
        boosted = engine.boost_recent_patterns(patterns, current_time=now)
        assert boosted[0]["confidence"] > 0.6

    def test_get_confidence_trend_unknown_for_missing_pattern(self) -> None:
        from file_organizer.services.intelligence.confidence import ConfidenceEngine

        engine = ConfidenceEngine()
        trend = engine.get_confidence_trend("no_such_pattern")
        assert trend["trend"] == "unknown"

    def test_get_confidence_trend_with_data(self) -> None:
        from file_organizer.services.intelligence.confidence import ConfidenceEngine

        engine = ConfidenceEngine()
        now = datetime.now(UTC)
        for i in range(10):
            ts = now - timedelta(days=20 - i)
            engine.track_usage("trend_pat", ts, success=i > 4)

        trend = engine.get_confidence_trend("trend_pat", current_time=now)
        assert "trend" in trend
        assert "direction" in trend

    def test_get_usage_data_returns_none_for_missing(self) -> None:
        from file_organizer.services.intelligence.confidence import ConfidenceEngine

        engine = ConfidenceEngine()
        assert engine.get_usage_data("missing") is None

    def test_get_usage_data_returns_tracked(self) -> None:
        from file_organizer.services.intelligence.confidence import ConfidenceEngine

        engine = ConfidenceEngine()
        engine.track_usage("tracked", datetime.now(UTC), success=True)
        assert engine.get_usage_data("tracked") is not None

    def test_clear_usage_data_specific(self) -> None:
        from file_organizer.services.intelligence.confidence import ConfidenceEngine

        engine = ConfidenceEngine()
        engine.track_usage("pat1", datetime.now(UTC), success=True)
        engine.track_usage("pat2", datetime.now(UTC), success=True)
        engine.clear_usage_data("pat1")
        assert engine.get_usage_data("pat1") is None
        assert engine.get_usage_data("pat2") is not None

    def test_clear_usage_data_all(self) -> None:
        from file_organizer.services.intelligence.confidence import ConfidenceEngine

        engine = ConfidenceEngine()
        engine.track_usage("x", datetime.now(UTC), success=True)
        engine.clear_usage_data()
        assert engine.get_usage_data("x") is None

    def test_get_stats(self) -> None:
        from file_organizer.services.intelligence.confidence import ConfidenceEngine

        engine = ConfidenceEngine()
        engine.track_usage("s1", datetime.now(UTC), success=True)
        engine.track_usage("s1", datetime.now(UTC), success=False)
        stats = engine.get_stats()
        assert stats["total_patterns"] == 1
        assert stats["total_uses"] == 2
        assert stats["successful_uses"] == 1

    def test_clear_stale_patterns(self) -> None:
        from file_organizer.services.intelligence.confidence import ConfidenceEngine

        engine = ConfidenceEngine()
        old_ts = datetime.now(UTC) - timedelta(days=200)
        engine.track_usage("stale_pat", old_ts, success=True)
        engine.track_usage("fresh_pat", datetime.now(UTC), success=True)
        removed = engine.clear_stale_patterns(days=30)
        assert removed == 1
        assert engine.get_usage_data("stale_pat") is None
        assert engine.get_usage_data("fresh_pat") is not None


# ---------------------------------------------------------------------------
# PatternScorer / ScoredPattern
# ---------------------------------------------------------------------------


class TestPatternScorer:
    def _make_pattern(
        self,
        pid: str,
        confidence: float,
        freq: float = 0.5,
        rec: float = 0.5,
        cons: float = 0.5,
    ):
        from file_organizer.services.intelligence.scoring import ScoredPattern

        return ScoredPattern(
            pattern_id=pid,
            pattern_data={},
            confidence=confidence,
            frequency_score=freq,
            recency_score=rec,
            consistency_score=cons,
        )

    def test_normalize_score_clamps(self) -> None:
        from file_organizer.services.intelligence.scoring import PatternScorer

        assert PatternScorer.normalize_score(1.5) == 1.0
        assert PatternScorer.normalize_score(-0.5) == 0.0
        assert PatternScorer.normalize_score(0.6) == 0.6

    def test_rank_patterns_descending(self) -> None:
        from file_organizer.services.intelligence.scoring import PatternScorer

        patterns = [
            self._make_pattern("a", 0.3),
            self._make_pattern("b", 0.9),
            self._make_pattern("c", 0.6),
        ]
        ranked = PatternScorer.rank_patterns(patterns)
        assert ranked[0].pattern_id == "b"
        assert ranked[-1].pattern_id == "a"

    def test_rank_patterns_ascending(self) -> None:
        from file_organizer.services.intelligence.scoring import PatternScorer

        patterns = [self._make_pattern("a", 0.3), self._make_pattern("b", 0.9)]
        ranked = PatternScorer.rank_patterns(patterns, reverse=False)
        assert ranked[0].pattern_id == "a"

    def test_rank_patterns_by_frequency(self) -> None:
        from file_organizer.services.intelligence.scoring import PatternScorer

        patterns = [
            self._make_pattern("a", 0.5, freq=0.2),
            self._make_pattern("b", 0.5, freq=0.9),
        ]
        ranked = PatternScorer.rank_patterns(patterns, key="frequency_score")
        assert ranked[0].pattern_id == "b"

    def test_filter_by_confidence_min_only(self) -> None:
        from file_organizer.services.intelligence.scoring import PatternScorer

        patterns = [
            self._make_pattern("a", 0.3),
            self._make_pattern("b", 0.7),
            self._make_pattern("c", 0.9),
        ]
        filtered = PatternScorer.filter_by_confidence(patterns, min_confidence=0.6)
        assert len(filtered) == 2
        ids = {p.pattern_id for p in filtered}
        assert "a" not in ids

    def test_filter_by_confidence_with_max(self) -> None:
        from file_organizer.services.intelligence.scoring import PatternScorer

        patterns = [
            self._make_pattern("a", 0.3),
            self._make_pattern("b", 0.7),
            self._make_pattern("c", 0.9),
        ]
        filtered = PatternScorer.filter_by_confidence(
            patterns, min_confidence=0.4, max_confidence=0.8
        )
        assert len(filtered) == 1
        assert filtered[0].pattern_id == "b"

    def test_get_top_patterns(self) -> None:
        from file_organizer.services.intelligence.scoring import PatternScorer

        patterns = [self._make_pattern(str(i), i * 0.1) for i in range(10)]
        top = PatternScorer.get_top_patterns(patterns, n=3)
        assert len(top) == 3
        assert top[0].confidence >= top[1].confidence

    def test_get_top_patterns_with_min_confidence(self) -> None:
        from file_organizer.services.intelligence.scoring import PatternScorer

        patterns = [self._make_pattern("lo", 0.2), self._make_pattern("hi", 0.8)]
        top = PatternScorer.get_top_patterns(patterns, n=5, min_confidence=0.5)
        assert len(top) == 1
        assert top[0].pattern_id == "hi"

    def test_calculate_weighted_score(self) -> None:
        from file_organizer.services.intelligence.scoring import PatternScorer

        scores = {"freq": 0.8, "recency": 0.6, "consistency": 0.4}
        weights = {"freq": 0.4, "recency": 0.3, "consistency": 0.3}
        result = PatternScorer.calculate_weighted_score(scores, weights)
        assert 0.0 <= result <= 1.0

    def test_calculate_weighted_score_zero_weights(self) -> None:
        from file_organizer.services.intelligence.scoring import PatternScorer

        assert PatternScorer.calculate_weighted_score({"a": 0.5}, {"a": 0.0}) == 0.0

    def test_compare_patterns(self) -> None:
        from file_organizer.services.intelligence.scoring import PatternScorer

        p1 = self._make_pattern("a", 0.3)
        p2 = self._make_pattern("b", 0.7)
        assert PatternScorer.compare_patterns(p1, p2) == -1
        assert PatternScorer.compare_patterns(p2, p1) == 1
        assert PatternScorer.compare_patterns(p1, p1) == 0

    def test_aggregate_scores_mean(self) -> None:
        from file_organizer.services.intelligence.scoring import PatternScorer

        patterns = [self._make_pattern("a", 0.4), self._make_pattern("b", 0.8)]
        agg = PatternScorer.aggregate_scores(patterns, aggregation="mean")
        assert agg["confidence"] == pytest.approx(0.6)

    def test_aggregate_scores_median(self) -> None:
        from file_organizer.services.intelligence.scoring import PatternScorer

        patterns = [
            self._make_pattern("a", 0.2),
            self._make_pattern("b", 0.6),
            self._make_pattern("c", 0.8),
        ]
        agg = PatternScorer.aggregate_scores(patterns, aggregation="median")
        assert agg["confidence"] == pytest.approx(0.6)

    def test_aggregate_scores_min_max(self) -> None:
        from file_organizer.services.intelligence.scoring import PatternScorer

        patterns = [self._make_pattern("a", 0.2), self._make_pattern("b", 0.9)]
        agg_min = PatternScorer.aggregate_scores(patterns, aggregation="min")
        agg_max = PatternScorer.aggregate_scores(patterns, aggregation="max")
        assert agg_min["confidence"] == pytest.approx(0.2)
        assert agg_max["confidence"] == pytest.approx(0.9)

    def test_aggregate_scores_empty(self) -> None:
        from file_organizer.services.intelligence.scoring import PatternScorer

        agg = PatternScorer.aggregate_scores([])
        assert agg["confidence"] == 0.0

    def test_scored_pattern_default_metadata(self) -> None:
        from file_organizer.services.intelligence.scoring import ScoredPattern

        sp = ScoredPattern(
            pattern_id="x",
            pattern_data={},
            confidence=0.5,
            frequency_score=0.4,
            recency_score=0.6,
            consistency_score=0.5,
        )
        assert sp.metadata == {}


# ---------------------------------------------------------------------------
# PreferenceStore
# ---------------------------------------------------------------------------


class TestPreferenceStore:
    def test_load_preferences_defaults_when_no_file(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path / "prefs")
        result = store.load_preferences()
        assert result is False
        stats = store.get_statistics()
        assert stats["total_directories"] == 0

    def test_save_and_reload_preferences(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path / "prefs")
        store.load_preferences()
        assert store.save_preferences() is True

        store2 = PreferenceStore(storage_path=tmp_path / "prefs")
        result = store2.load_preferences()
        assert result is True

    def test_add_and_get_preference(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path / "prefs")
        store.load_preferences()

        test_dir = tmp_path / "documents"
        test_dir.mkdir()

        store.add_preference(
            test_dir,
            {
                "folder_mappings": {"pdf": "docs"},
                "naming_patterns": {},
                "category_overrides": {},
                "confidence": 0.8,
                "correction_count": 1,
            },
        )
        pref = store.get_preference(test_dir, fallback_to_parent=False)
        assert pref is not None
        assert pref["folder_mappings"]["pdf"] == "docs"

    def test_get_preference_parent_fallback(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path / "prefs")
        store.load_preferences()

        parent_dir = tmp_path / "parent"
        parent_dir.mkdir()
        child_dir = parent_dir / "child"
        child_dir.mkdir()

        store.add_preference(
            parent_dir,
            {
                "folder_mappings": {"txt": "notes"},
                "naming_patterns": {},
                "category_overrides": {},
                "confidence": 0.7,
            },
        )

        pref = store.get_preference(child_dir, fallback_to_parent=True)
        assert pref is not None
        assert pref.get("folder_mappings", {}).get("txt") == "notes"

    def test_get_preference_returns_global_when_not_found(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path / "prefs")
        store.load_preferences()
        unknown_dir = tmp_path / "unknown" / "dir"
        pref = store.get_preference(unknown_dir, fallback_to_parent=False)
        assert pref is not None
        assert isinstance(pref, dict)

    def test_update_confidence_success(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path / "prefs")
        store.load_preferences()

        test_dir = tmp_path / "conf_test"
        test_dir.mkdir()
        store.add_preference(
            test_dir,
            {
                "folder_mappings": {},
                "naming_patterns": {},
                "category_overrides": {},
                "confidence": 0.5,
            },
        )

        store.update_confidence(test_dir, success=True)
        pref = store.get_preference(test_dir, fallback_to_parent=False)
        assert pref is not None
        assert pref["confidence"] > 0.5

    def test_update_confidence_failure(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path / "prefs")
        store.load_preferences()

        test_dir = tmp_path / "conf_fail"
        test_dir.mkdir()
        store.add_preference(
            test_dir,
            {
                "folder_mappings": {},
                "naming_patterns": {},
                "category_overrides": {},
                "confidence": 0.5,
            },
        )

        store.update_confidence(test_dir, success=False)
        pref = store.get_preference(test_dir, fallback_to_parent=False)
        assert pref is not None
        assert pref["confidence"] < 0.5

    def test_resolve_conflicts_returns_highest_score(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path / "prefs")
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        low_conf = {
            "folder_mappings": {"a": "low"},
            "naming_patterns": {},
            "category_overrides": {},
            "confidence": 0.2,
            "correction_count": 1,
            "updated": now,
            "created": now,
        }
        high_conf = {
            "folder_mappings": {"a": "high"},
            "naming_patterns": {},
            "category_overrides": {},
            "confidence": 0.9,
            "correction_count": 10,
            "updated": now,
            "created": now,
        }
        resolved = store.resolve_conflicts([low_conf, high_conf])
        assert resolved["folder_mappings"]["a"] == "high"

    def test_resolve_conflicts_empty(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path / "prefs")
        assert store.resolve_conflicts([]) == {}

    def test_export_json(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path / "prefs")
        store.load_preferences()
        out = tmp_path / "export.json"
        result = store.export_json(out)
        assert result is True
        assert out.exists()
        data = json.loads(out.read_text())
        assert "version" in data

    def test_import_json(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path / "prefs")
        store.load_preferences()
        out = tmp_path / "export.json"
        store.export_json(out)

        store2 = PreferenceStore(storage_path=tmp_path / "prefs2")
        result = store2.import_json(out)
        assert result is True

    def test_import_json_missing_file(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path / "prefs")
        result = store.import_json(tmp_path / "ghost.json")
        assert result is False

    def test_import_json_invalid_schema(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.preference_store import PreferenceStore

        bad_file = tmp_path / "bad.json"
        bad_file.write_text('{"not": "valid"}')
        store = PreferenceStore(storage_path=tmp_path / "prefs")
        result = store.import_json(bad_file)
        assert result is False

    def test_clear_preferences(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path / "prefs")
        store.load_preferences()
        test_dir = tmp_path / "to_clear"
        test_dir.mkdir()
        store.add_preference(
            test_dir,
            {
                "folder_mappings": {"x": "y"},
                "naming_patterns": {},
                "category_overrides": {},
                "confidence": 0.5,
            },
        )
        store.clear_preferences()
        stats = store.get_statistics()
        assert stats["total_directories"] == 0

    def test_list_directory_preferences(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path / "prefs")
        store.load_preferences()
        d1 = tmp_path / "dir1"
        d1.mkdir()
        store.add_preference(
            d1,
            {
                "folder_mappings": {},
                "naming_patterns": {},
                "category_overrides": {},
                "confidence": 0.5,
            },
        )
        prefs = store.list_directory_preferences()
        assert len(prefs) == 1

    def test_load_from_backup_when_primary_corrupt(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path / "prefs")
        store.load_preferences()
        store.save_preferences()

        store.preference_file.write_text("{invalid json}")
        store2 = PreferenceStore(storage_path=tmp_path / "prefs")
        store2.load_preferences()
        assert store2._loaded is True

    def test_get_statistics(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.preference_store import PreferenceStore

        store = PreferenceStore(storage_path=tmp_path / "prefs")
        store.load_preferences()
        stats = store.get_statistics()
        assert "total_directories" in stats
        assert "schema_version" in stats


# ---------------------------------------------------------------------------
# ProfileManager / Profile
# ---------------------------------------------------------------------------


class TestProfileManager:
    def test_default_profile_created(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_manager import ProfileManager

        pm = ProfileManager(storage_path=tmp_path / "profiles")
        profile = pm.get_profile("default")
        assert profile is not None
        assert profile.profile_name == "default"

    def test_create_profile(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_manager import ProfileManager

        pm = ProfileManager(storage_path=tmp_path / "profiles")
        p = pm.create_profile("work", "Work profile")
        assert p is not None
        assert p.profile_name == "work"

    def test_create_duplicate_profile_returns_none(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_manager import ProfileManager

        pm = ProfileManager(storage_path=tmp_path / "profiles")
        pm.create_profile("dup", "First")
        result = pm.create_profile("dup", "Second")
        assert result is None

    def test_list_profiles(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_manager import ProfileManager

        pm = ProfileManager(storage_path=tmp_path / "profiles")
        pm.create_profile("alpha", "Alpha profile")
        pm.create_profile("beta", "Beta profile")
        profiles = pm.list_profiles()
        names = [p.profile_name for p in profiles]
        assert "default" in names
        assert "alpha" in names
        assert "beta" in names

    def test_get_active_profile(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_manager import ProfileManager

        pm = ProfileManager(storage_path=tmp_path / "profiles")
        active = pm.get_active_profile()
        assert active is not None

    def test_switch_active_profile(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_manager import ProfileManager

        pm = ProfileManager(storage_path=tmp_path / "profiles")
        pm.create_profile("second", "Second profile")
        success = pm.activate_profile("second")
        assert success is True
        active = pm.get_active_profile()
        assert active is not None
        assert active.profile_name == "second"

    def test_switch_to_nonexistent_profile_fails(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_manager import ProfileManager

        pm = ProfileManager(storage_path=tmp_path / "profiles")
        result = pm.activate_profile("nonexistent")
        assert result is False

    def test_delete_profile(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_manager import ProfileManager

        pm = ProfileManager(storage_path=tmp_path / "profiles")
        pm.create_profile("to_delete", "Will be deleted")
        result = pm.delete_profile("to_delete")
        assert result is True
        assert pm.get_profile("to_delete") is None

    def test_delete_active_profile_fails(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_manager import ProfileManager

        pm = ProfileManager(storage_path=tmp_path / "profiles")
        result = pm.delete_profile("default")
        assert result is False

    def test_update_profile(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_manager import ProfileManager

        pm = ProfileManager(storage_path=tmp_path / "profiles")
        pm.create_profile("updatable", "Original description")
        result = pm.update_profile(
            "updatable",
            description="Updated description",
        )
        assert result is True
        p = pm.get_profile("updatable")
        assert p is not None
        assert p.description == "Updated description"

    def test_update_nonexistent_profile_fails(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_manager import ProfileManager

        pm = ProfileManager(storage_path=tmp_path / "profiles")
        result = pm.update_profile("ghost", description="nope")
        assert result is False

    def test_profile_validate_valid(self) -> None:
        from file_organizer.services.intelligence.profile_manager import Profile

        p = Profile(profile_name="valid", description="A valid profile")
        assert p.validate() is True

    def test_profile_validate_empty_name_fails(self) -> None:
        from file_organizer.services.intelligence.profile_manager import Profile

        p = Profile(profile_name="", description="desc")
        assert p.validate() is False

    def test_profile_validate_empty_description_fails(self) -> None:
        from file_organizer.services.intelligence.profile_manager import Profile

        p = Profile(profile_name="name", description="")
        assert p.validate() is False

    def test_profile_to_and_from_dict(self) -> None:
        from file_organizer.services.intelligence.profile_manager import Profile

        original = Profile(profile_name="roundtrip", description="Test roundtrip")
        d = original.to_dict()
        restored = Profile.from_dict(d)
        assert restored.profile_name == original.profile_name
        assert restored.description == original.description


# ---------------------------------------------------------------------------
# ProfileMigrator
# ---------------------------------------------------------------------------


class TestProfileMigrator:
    def _make_pm(self, tmp_path: Path):
        from file_organizer.services.intelligence.profile_manager import ProfileManager

        return ProfileManager(storage_path=tmp_path / "profiles")

    def test_migrate_version_already_current(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_migrator import ProfileMigrator

        pm = self._make_pm(tmp_path)
        migrator = ProfileMigrator(pm)
        result = migrator.migrate_version("default", "1.0")
        assert result is True

    def test_migrate_version_unsupported_target(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_migrator import ProfileMigrator

        pm = self._make_pm(tmp_path)
        migrator = ProfileMigrator(pm)
        result = migrator.migrate_version("default", "99.0")
        assert result is False

    def test_migrate_version_nonexistent_profile(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_migrator import ProfileMigrator

        pm = self._make_pm(tmp_path)
        migrator = ProfileMigrator(pm)
        result = migrator.migrate_version("ghost", "1.0")
        assert result is False

    def test_backup_before_migration(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_migrator import ProfileMigrator

        pm = self._make_pm(tmp_path)
        migrator = ProfileMigrator(pm)
        profile = pm.get_profile("default")
        backup_path = migrator.backup_before_migration(profile)
        assert backup_path is not None
        assert backup_path.exists()

    def test_rollback_migration_success(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_migrator import ProfileMigrator

        pm = self._make_pm(tmp_path)
        migrator = ProfileMigrator(pm)
        profile = pm.get_profile("default")
        backup_path = migrator.backup_before_migration(profile)
        result = migrator.rollback_migration("default", backup_path)
        assert result is True

    def test_rollback_migration_missing_backup(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_migrator import ProfileMigrator

        pm = self._make_pm(tmp_path)
        migrator = ProfileMigrator(pm)
        result = migrator.rollback_migration("default", tmp_path / "ghost_backup.json")
        assert result is False

    def test_validate_migration_valid_profile(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_migrator import ProfileMigrator

        pm = self._make_pm(tmp_path)
        migrator = ProfileMigrator(pm)
        result = migrator.validate_migration("default")
        assert result is True

    def test_validate_migration_nonexistent_profile(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_migrator import ProfileMigrator

        pm = self._make_pm(tmp_path)
        migrator = ProfileMigrator(pm)
        result = migrator.validate_migration("no_such_profile")
        assert result is False

    def test_get_migration_history_empty(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_migrator import ProfileMigrator

        pm = self._make_pm(tmp_path)
        migrator = ProfileMigrator(pm)
        history = migrator.get_migration_history("default")
        assert history == []

    def test_get_migration_history_nonexistent(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_migrator import ProfileMigrator

        pm = self._make_pm(tmp_path)
        migrator = ProfileMigrator(pm)
        assert migrator.get_migration_history("ghost") is None

    def test_list_backups_empty_when_no_backups(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_migrator import ProfileMigrator

        pm = self._make_pm(tmp_path)
        migrator = ProfileMigrator(pm)
        backups = migrator.list_backups()
        assert backups == []

    def test_list_backups_after_backup(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_migrator import ProfileMigrator

        pm = self._make_pm(tmp_path)
        migrator = ProfileMigrator(pm)
        profile = pm.get_profile("default")
        migrator.backup_before_migration(profile)
        backups = migrator.list_backups()
        assert len(backups) >= 1

    def test_list_backups_filtered_by_name(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_migrator import ProfileMigrator

        pm = self._make_pm(tmp_path)
        pm.create_profile("other", "Other profile")
        migrator = ProfileMigrator(pm)
        default_profile = pm.get_profile("default")
        other_profile = pm.get_profile("other")
        migrator.backup_before_migration(default_profile)
        migrator.backup_before_migration(other_profile)
        backups = migrator.list_backups("default")
        assert all("default" in b.name for b in backups)

    def test_register_migration_function(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_migrator import ProfileMigrator

        pm = self._make_pm(tmp_path)
        migrator = ProfileMigrator(pm)

        def dummy_migration(data):
            return data

        migrator.register_migration("1.0", "2.0", dummy_migration)
        assert "1.0->2.0" in migrator._migration_functions


# ---------------------------------------------------------------------------
# ProfileMerger
# ---------------------------------------------------------------------------


class TestProfileMerger:
    def _make_pm(self, tmp_path: Path):
        from file_organizer.services.intelligence.profile_manager import ProfileManager

        return ProfileManager(storage_path=tmp_path / "profiles")

    def test_merge_profiles_basic(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_merger import ProfileMerger

        pm = self._make_pm(tmp_path)
        pm.create_profile("p1", "Profile One")
        pm.create_profile("p2", "Profile Two")
        merger = ProfileMerger(pm)
        merged = merger.merge_profiles(["p1", "p2"], merge_strategy="confident")
        assert merged is not None

    def test_merge_profiles_too_few_profiles(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_merger import ProfileMerger

        pm = self._make_pm(tmp_path)
        merger = ProfileMerger(pm)
        result = merger.merge_profiles(["only_one"])
        assert result is None

    def test_merge_profiles_invalid_strategy(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_merger import ProfileMerger

        pm = self._make_pm(tmp_path)
        pm.create_profile("q1", "Q1")
        pm.create_profile("q2", "Q2")
        merger = ProfileMerger(pm)
        result = merger.merge_profiles(["q1", "q2"], merge_strategy="bad_strategy")
        assert result is None

    def test_merge_profiles_nonexistent_profile(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_merger import ProfileMerger

        pm = self._make_pm(tmp_path)
        pm.create_profile("exists", "Exists")
        merger = ProfileMerger(pm)
        result = merger.merge_profiles(["exists", "ghost"])
        assert result is None

    def test_merge_strategies(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_merger import ProfileMerger

        for strategy in ["recent", "frequent", "confident", "first", "last"]:
            pm = self._make_pm(tmp_path / strategy)
            pm.create_profile("s1", "Source 1")
            pm.create_profile("s2", "Source 2")
            merger = ProfileMerger(pm)
            result = merger.merge_profiles(["s1", "s2"], merge_strategy=strategy)
            assert result is not None, f"Strategy {strategy} should succeed"

    def test_merge_with_custom_output_name(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_merger import ProfileMerger

        pm = self._make_pm(tmp_path)
        pm.create_profile("m1", "M1")
        pm.create_profile("m2", "M2")
        merger = ProfileMerger(pm)
        merged = merger.merge_profiles(["m1", "m2"], output_name="custom_merged")
        assert merged is not None
        assert merged.profile_name == "custom_merged"

    def test_merge_profiles_with_preferences(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_merger import ProfileMerger

        pm = self._make_pm(tmp_path)
        pm.create_profile("pref1", "Pref 1")
        pm.update_profile(
            "pref1",
            preferences={
                "global": {"folder_rule": "by_type"},
                "directory_specific": {},
            },
        )
        pm.create_profile("pref2", "Pref 2")
        pm.update_profile(
            "pref2",
            preferences={
                "global": {"folder_rule": "by_date"},
                "directory_specific": {},
            },
        )
        merger = ProfileMerger(pm)
        result = merger.merge_profiles(["pref1", "pref2"])
        assert result is not None


# ---------------------------------------------------------------------------
# ProfileExporter
# ---------------------------------------------------------------------------


class TestProfileExporter:
    def _make_pm(self, tmp_path: Path):
        from file_organizer.services.intelligence.profile_manager import ProfileManager

        return ProfileManager(storage_path=tmp_path / "profiles")

    def test_export_profile_success(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_exporter import ProfileExporter

        pm = self._make_pm(tmp_path)
        exporter = ProfileExporter(pm)
        out = tmp_path / "exports" / "default_export.json"
        result = exporter.export_profile("default", out)
        assert result is True
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["profile_name"] == "default"
        assert "exported_at" in data

    def test_export_profile_nonexistent(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_exporter import ProfileExporter

        pm = self._make_pm(tmp_path)
        exporter = ProfileExporter(pm)
        result = exporter.export_profile("ghost", tmp_path / "ghost_export.json")
        assert result is False

    def test_export_selective_global(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_exporter import ProfileExporter

        pm = self._make_pm(tmp_path)
        exporter = ProfileExporter(pm)
        out = tmp_path / "selective.json"
        result = exporter.export_selective("default", out, ["global"])
        assert result is True
        data = json.loads(out.read_text())
        assert data["export_type"] == "selective"
        assert "global" in data["included_preferences"]

    def test_export_selective_nonexistent_profile(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_exporter import ProfileExporter

        pm = self._make_pm(tmp_path)
        exporter = ProfileExporter(pm)
        result = exporter.export_selective("ghost", tmp_path / "sel.json", ["global"])
        assert result is False

    def test_validate_export_valid(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_exporter import ProfileExporter

        pm = self._make_pm(tmp_path)
        exporter = ProfileExporter(pm)
        out = tmp_path / "valid_export.json"
        exporter.export_profile("default", out)
        assert exporter.validate_export(out) is True

    def test_validate_export_missing_file(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_exporter import ProfileExporter

        pm = self._make_pm(tmp_path)
        exporter = ProfileExporter(pm)
        assert exporter.validate_export(tmp_path / "ghost.json") is False

    def test_preview_export(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_exporter import ProfileExporter

        pm = self._make_pm(tmp_path)
        exporter = ProfileExporter(pm)
        preview = exporter.preview_export("default")
        assert preview is not None
        assert preview["profile_name"] == "default"
        assert "statistics" in preview
        assert "export_size_estimate" in preview

    def test_preview_export_nonexistent(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_exporter import ProfileExporter

        pm = self._make_pm(tmp_path)
        exporter = ProfileExporter(pm)
        result = exporter.preview_export("ghost")
        assert result is None

    def test_export_multiple(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.profile_exporter import ProfileExporter

        pm = self._make_pm(tmp_path)
        pm.create_profile("ex1", "Export 1")
        pm.create_profile("ex2", "Export 2")
        exporter = ProfileExporter(pm)
        out_dir = tmp_path / "multi_exports"
        results = exporter.export_multiple(["default", "ex1", "ex2"], out_dir)
        assert results["default"] is True
        assert results["ex1"] is True
        assert results["ex2"] is True
        assert (out_dir / "default.json").exists()


# ---------------------------------------------------------------------------
# FolderPreferenceLearner
# ---------------------------------------------------------------------------


class TestFolderPreferenceLearner:
    def test_track_folder_choice(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.folder_learner import FolderPreferenceLearner

        learner = FolderPreferenceLearner(storage_path=tmp_path / "folder_prefs.json")
        folder = tmp_path / "Documents"
        folder.mkdir()
        learner.track_folder_choice("pdf", folder)
        assert learner.total_choices == 1

    def test_get_preferred_folder_with_confidence(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.folder_learner import FolderPreferenceLearner

        learner = FolderPreferenceLearner(storage_path=tmp_path / "folder_prefs.json")
        folder = tmp_path / "PDFs"
        folder.mkdir()
        for _ in range(10):
            learner.track_folder_choice("pdf", folder)

        result = learner.get_preferred_folder("pdf", confidence_threshold=0.6)
        assert result is not None
        assert result == folder.resolve()

    def test_get_preferred_folder_below_threshold(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.folder_learner import FolderPreferenceLearner

        learner = FolderPreferenceLearner(storage_path=tmp_path / "folder_prefs.json")
        f1 = tmp_path / "F1"
        f2 = tmp_path / "F2"
        f1.mkdir()
        f2.mkdir()
        learner.track_folder_choice("txt", f1)
        learner.track_folder_choice("txt", f2)
        result = learner.get_preferred_folder("txt", confidence_threshold=0.9)
        assert result is None

    def test_get_preferred_folder_unknown_type(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.folder_learner import FolderPreferenceLearner

        learner = FolderPreferenceLearner(storage_path=tmp_path / "folder_prefs.json")
        assert learner.get_preferred_folder("xyz") is None

    def test_get_folder_confidence(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.folder_learner import FolderPreferenceLearner

        learner = FolderPreferenceLearner(storage_path=tmp_path / "folder_prefs.json")
        f = tmp_path / "Target"
        f.mkdir()
        other = tmp_path / "Other"
        other.mkdir()
        for _ in range(3):
            learner.track_folder_choice("docx", f)
        learner.track_folder_choice("docx", other)

        conf = learner.get_folder_confidence("docx", f)
        assert conf == pytest.approx(0.75)

    def test_get_folder_confidence_unknown(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.folder_learner import FolderPreferenceLearner

        learner = FolderPreferenceLearner(storage_path=tmp_path / "folder_prefs.json")
        assert learner.get_folder_confidence("unknown", tmp_path) == 0.0

    def test_analyze_organization_patterns(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.folder_learner import FolderPreferenceLearner

        learner = FolderPreferenceLearner(storage_path=tmp_path / "folder_prefs.json")
        f = tmp_path / "Sorted"
        f.mkdir()
        for _ in range(5):
            learner.track_folder_choice("jpg", f)

        analysis = learner.analyze_organization_patterns()
        assert "total_choices" in analysis
        assert analysis["total_choices"] == 5
        assert analysis["file_types_tracked"] >= 1

    def test_track_folder_choice_with_context(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.folder_learner import FolderPreferenceLearner

        learner = FolderPreferenceLearner(storage_path=tmp_path / "folder_prefs.json")
        folder = tmp_path / "Ctx"
        folder.mkdir()
        learner.track_folder_choice("py", folder, context={"pattern": "project_*"})
        assert learner.total_choices == 1

    def test_persistence_across_instances(self, tmp_path: Path) -> None:
        from file_organizer.services.intelligence.folder_learner import FolderPreferenceLearner

        storage = tmp_path / "folder_prefs.json"
        learner = FolderPreferenceLearner(storage_path=storage)
        folder = tmp_path / "Persist"
        folder.mkdir()
        for _ in range(5):
            learner.track_folder_choice("mp4", folder)

        learner2 = FolderPreferenceLearner(storage_path=storage)
        pref = learner2.get_preferred_folder("mp4", confidence_threshold=0.5)
        assert pref is not None
