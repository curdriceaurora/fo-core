"""Integration tests for profile_merger.py — branch coverage.

Targets uncovered lines: 53, 114-123, 133, 138-140, 164->163, 176->161,
186-202, 226-242, 267-281, 335->334, 339, 359->358, 360->358, 375->377,
378->367, 383-389, 393-399, 418, 429, 433-435, 487, 495-497.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from services.intelligence.profile_manager import Profile, ProfileManager
from services.intelligence.profile_merger import MergeStrategy, ProfileMerger

pytestmark = [pytest.mark.ci, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def manager(tmp_path: Path) -> ProfileManager:
    return ProfileManager(storage_path=tmp_path / "profiles")


@pytest.fixture()
def merger(manager: ProfileManager) -> ProfileMerger:
    return ProfileMerger(profile_manager=manager)


def _make_profile(
    manager: ProfileManager,
    name: str,
    *,
    global_prefs: dict | None = None,
    dir_prefs: dict | None = None,
    patterns: dict | None = None,
    confidence: dict | None = None,
) -> None:
    """Create a profile and update it with optional data fields."""
    manager.create_profile(name, f"{name} description")
    update_kwargs: dict = {}
    if global_prefs is not None or dir_prefs is not None:
        update_kwargs["preferences"] = {
            "global": global_prefs or {},
            "directory_specific": dir_prefs or {},
        }
    if patterns is not None:
        update_kwargs["learned_patterns"] = patterns
    if confidence is not None:
        update_kwargs["confidence_data"] = confidence
    if update_kwargs:
        manager.update_profile(name, **update_kwargs)


# ---------------------------------------------------------------------------
# _get_current_timestamp (line 53)
# ---------------------------------------------------------------------------


class TestGetCurrentTimestamp:
    def test_returns_z_terminated_iso_string(self, merger: ProfileMerger) -> None:
        ts = merger._get_current_timestamp()
        assert ts.endswith("Z")
        assert "T" in ts


# ---------------------------------------------------------------------------
# merge_profiles — too few profiles (line 72-74 already covered; confirm None)
# ---------------------------------------------------------------------------


class TestMergeProfilesEdgeCases:
    def test_merge_empty_list_returns_none(self, merger: ProfileMerger) -> None:
        result = merger.merge_profiles([])
        assert result is None

    def test_merge_single_profile_returns_none(self, merger: ProfileMerger) -> None:
        result = merger.merge_profiles(["default"])
        assert result is None

    def test_merge_invalid_strategy_returns_none(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        manager.create_profile("A", "a")
        result = merger.merge_profiles(["default", "A"], merge_strategy="no_such_strategy")
        assert result is None

    def test_merge_missing_profile_in_list_returns_none(self, merger: ProfileMerger) -> None:
        result = merger.merge_profiles(["default", "does_not_exist_xyz"])
        assert result is None

    # lines 114-123: output profile already exists → update path
    def test_merge_into_existing_output_name(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        """Merging into an output_name that already exists hits the update-existing branch."""
        manager.create_profile("Src", "src")
        # Pre-create the output profile so create_profile returns None
        manager.create_profile("already_exists", "pre-existing merged output")
        result = merger.merge_profiles(
            ["default", "Src"],
            output_name="already_exists",
        )
        # Should succeed via the update path
        assert result is not None
        assert result.profile_name == "already_exists"

    # line 133: update_profile fails after create succeeds
    def test_merge_returns_none_when_update_fails_on_new_profile(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        manager.create_profile("B", "b")
        with patch.object(manager, "update_profile", return_value=False):
            result = merger.merge_profiles(["default", "B"], output_name="fresh_output")
        assert result is None

    # lines 138-140: exception propagation inside merge
    def test_merge_returns_none_on_unexpected_error(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        manager.create_profile("C", "c")
        with patch.object(manager, "get_profile", side_effect=OSError("simulated disk error")):
            result = merger.merge_profiles(["default", "C"])
        assert result is None


# ---------------------------------------------------------------------------
# _merge_preferences — global prefs path (lines 164-178)
# ---------------------------------------------------------------------------


class TestMergePreferencesGlobal:
    def test_global_prefs_merged_with_conflicting_values(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        """Two profiles with same global key but different values → resolution runs."""
        _make_profile(
            manager, "G1", global_prefs={"sort_order": "asc"}, confidence={"sort_order": 0.7}
        )
        _make_profile(
            manager, "G2", global_prefs={"sort_order": "desc"}, confidence={"sort_order": 0.9}
        )

        result = merger.merge_profiles(["G1", "G2"], merge_strategy="confident")
        assert result is not None
        global_prefs = (result.preferences or {}).get("global", {})
        # CONFIDENT strategy picks highest confidence value
        assert global_prefs.get("sort_order") == "desc"

    def test_global_prefs_non_overlapping_keys_all_present(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        _make_profile(manager, "H1", global_prefs={"theme": "dark"})
        _make_profile(manager, "H2", global_prefs={"lang": "en"})

        result = merger.merge_profiles(["H1", "H2"])
        assert result is not None
        global_prefs = (result.preferences or {}).get("global", {})
        assert "theme" in global_prefs
        assert "lang" in global_prefs

    def test_global_prefs_first_strategy_picks_first(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        _make_profile(manager, "F1", global_prefs={"key": "first_val"})
        _make_profile(manager, "F2", global_prefs={"key": "second_val"})

        result = merger.merge_profiles(["F1", "F2"], merge_strategy="first")
        assert result is not None
        global_prefs = (result.preferences or {}).get("global", {})
        assert global_prefs.get("key") == "first_val"

    def test_global_prefs_last_strategy_picks_last(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        _make_profile(manager, "L1", global_prefs={"key": "first_val"})
        _make_profile(manager, "L2", global_prefs={"key": "last_val"})

        result = merger.merge_profiles(["L1", "L2"], merge_strategy="last")
        assert result is not None
        global_prefs = (result.preferences or {}).get("global", {})
        assert global_prefs.get("key") == "last_val"

    def test_global_prefs_recent_strategy(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        _make_profile(manager, "R1", global_prefs={"key": "old"})
        _make_profile(manager, "R2", global_prefs={"key": "new"})

        result = merger.merge_profiles(["R1", "R2"], merge_strategy="recent")
        assert result is not None
        global_prefs = (result.preferences or {}).get("global", {})
        assert "key" in global_prefs


# ---------------------------------------------------------------------------
# _merge_preferences — directory-specific prefs (lines 186-202)
# ---------------------------------------------------------------------------


class TestMergePreferencesDirectorySpecific:
    def test_dir_specific_prefs_merged(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        # noqa: G2 (adversarial path literal — testing directory-specific prefs)
        docs_dir = "/docs"  # noqa: G2 (path literal used as test input key)
        _make_profile(manager, "D1", dir_prefs={docs_dir: {"ext": ".pdf"}})
        _make_profile(manager, "D2", dir_prefs={docs_dir: {"ext": ".txt"}})

        result = merger.merge_profiles(["D1", "D2"], merge_strategy="first")
        assert result is not None
        dir_prefs = (result.preferences or {}).get("directory_specific", {})
        assert docs_dir in dir_prefs

    def test_dir_specific_non_overlapping_keys_merged(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        _make_profile(manager, "E1", dir_prefs={"/docs": {"ext": ".pdf"}})
        _make_profile(manager, "E2", dir_prefs={"/images": {"ext": ".jpg"}})

        result = merger.merge_profiles(["E1", "E2"])
        assert result is not None
        dir_prefs = (result.preferences or {}).get("directory_specific", {})
        assert "/docs" in dir_prefs
        assert "/images" in dir_prefs


# ---------------------------------------------------------------------------
# _merge_learned_patterns (lines 226-242)
# ---------------------------------------------------------------------------


class TestMergeLearnedPatterns:
    def test_patterns_merged_when_both_profiles_have_same_key(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        _make_profile(manager, "P1", patterns={"file_type": "pdf"})
        _make_profile(manager, "P2", patterns={"file_type": "docx"})

        result = merger.merge_profiles(["P1", "P2"], merge_strategy="first")
        assert result is not None
        patterns = result.learned_patterns or {}
        assert patterns.get("file_type") == "pdf"

    def test_patterns_merged_non_overlapping_keys(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        _make_profile(manager, "Q1", patterns={"pattern_a": "val_a"})
        _make_profile(manager, "Q2", patterns={"pattern_b": "val_b"})

        result = merger.merge_profiles(["Q1", "Q2"])
        assert result is not None
        patterns = result.learned_patterns or {}
        assert "pattern_a" in patterns
        assert "pattern_b" in patterns

    def test_patterns_merged_with_confident_strategy(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        _make_profile(
            manager,
            "S1",
            patterns={"ext": "pdf"},
            confidence={"ext": 0.3},
        )
        _make_profile(
            manager,
            "S2",
            patterns={"ext": "docx"},
            confidence={"ext": 0.95},
        )

        result = merger.merge_profiles(["S1", "S2"], merge_strategy="confident")
        assert result is not None
        patterns = result.learned_patterns or {}
        assert patterns.get("ext") == "docx"


# ---------------------------------------------------------------------------
# _merge_confidence_data branches (lines 267-281)
# ---------------------------------------------------------------------------


class TestMergeConfidenceData:
    def test_confident_strategy_uses_max_confidence(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        _make_profile(manager, "C1", confidence={"accuracy": 0.4})
        _make_profile(manager, "C2", confidence={"accuracy": 0.9})

        result = merger.merge_profiles(["C1", "C2"], merge_strategy="confident")
        assert result is not None
        confidence = result.confidence_data or {}
        assert confidence.get("accuracy") == pytest.approx(0.9)

    def test_recent_strategy_uses_last_confidence(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        _make_profile(manager, "T1", confidence={"precision": 0.6})
        _make_profile(manager, "T2", confidence={"precision": 0.75})

        result = merger.merge_profiles(["T1", "T2"], merge_strategy="recent")
        assert result is not None
        confidence = result.confidence_data or {}
        # RECENT uses confidence_values[-1] — value from last profile
        assert confidence.get("precision") == pytest.approx(0.75)

    def test_other_strategy_uses_average_confidence(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        _make_profile(manager, "U1", confidence={"recall": 0.4})
        _make_profile(manager, "U2", confidence={"recall": 0.8})

        result = merger.merge_profiles(["U1", "U2"], merge_strategy="first")
        assert result is not None
        confidence = result.confidence_data or {}
        # FIRST → average branch for confidence merging
        assert confidence.get("recall") == pytest.approx(0.6)

    def test_frequent_strategy_uses_average_confidence(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        _make_profile(manager, "V1", confidence={"score": 0.3})
        _make_profile(manager, "V2", confidence={"score": 0.7})

        result = merger.merge_profiles(["V1", "V2"], merge_strategy="frequent")
        assert result is not None
        confidence = result.confidence_data or {}
        assert confidence.get("score") == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# resolve_conflicts — FREQUENT strategy (lines 325-339)
# ---------------------------------------------------------------------------


class TestResolveConflictsFrequent:
    def test_frequent_strategy_picks_most_common_value(self, merger: ProfileMerger) -> None:
        prefs = [
            {"value": "A", "metadata": {}},
            {"value": "B", "metadata": {}},
            {"value": "A", "metadata": {}},
        ]
        result = merger.resolve_conflicts(prefs, strategy=MergeStrategy.FREQUENT)
        assert result == "A"

    def test_frequent_strategy_single_item_returned(self, merger: ProfileMerger) -> None:
        prefs = [{"value": "only", "metadata": {}}]
        result = merger.resolve_conflicts(prefs, strategy=MergeStrategy.FREQUENT)
        assert result == "only"

    def test_frequent_strategy_tie_picks_first_encountered(self, merger: ProfileMerger) -> None:
        prefs = [
            {"value": "X", "metadata": {}},
            {"value": "Y", "metadata": {}},
        ]
        result = merger.resolve_conflicts(prefs, strategy=MergeStrategy.FREQUENT)
        # Both appear once — max() on dict.items() returns the first key on a tie
        # (Python 3.7+ dict preserves insertion order, so "X" is always first).
        assert result == "X"

    def test_frequent_end_to_end_via_merge(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        """Exercises FREQUENT through the full merge pipeline."""
        _make_profile(manager, "Freq1", global_prefs={"category": "docs"})
        _make_profile(manager, "Freq2", global_prefs={"category": "docs"})
        _make_profile(manager, "Freq3", global_prefs={"category": "images"})

        result = merger.merge_profiles(["Freq1", "Freq2", "Freq3"], merge_strategy="frequent")
        assert result is not None
        global_prefs = (result.preferences or {}).get("global", {})
        # "docs" appears twice, "images" once → FREQUENT picks "docs"
        assert global_prefs.get("category") == "docs"


# ---------------------------------------------------------------------------
# preserve_high_confidence — all three inner branches (lines 359-399)
# ---------------------------------------------------------------------------


class TestPreserveHighConfidence:
    def _make_merged_profile(self, manager: ProfileManager) -> Profile:
        """Return a blank merged profile that has empty dict fields."""
        manager.create_profile("merged_out", "merged")
        manager.update_profile(
            "merged_out",
            preferences={"global": {}, "directory_specific": {}},
            learned_patterns={},
            confidence_data={},
        )
        p = manager.get_profile("merged_out")
        assert p is not None
        return p

    def test_high_confidence_global_pref_preserved(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        """High-confidence key in global prefs is copied to merged profile (lines 373-379)."""
        _make_profile(
            manager,
            "HighGlobal",
            global_prefs={"theme": "dark"},
            confidence={"theme": 0.95},
        )
        source = manager.get_profile("HighGlobal")
        assert source is not None
        merged = self._make_merged_profile(manager)

        merger.preserve_high_confidence(merged, [source], confidence_threshold=0.8)

        assert (merged.preferences or {}).get("global", {}).get("theme") == "dark"
        assert (merged.confidence_data or {}).get("theme") == pytest.approx(0.95)

    def test_high_confidence_dir_specific_pref_preserved(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        """High-confidence key in directory_specific prefs is copied (lines 382-389)."""
        _make_profile(
            manager,
            "HighDir",
            dir_prefs={"/docs": "archive"},
            confidence={"/docs": 0.9},
        )
        source = manager.get_profile("HighDir")
        assert source is not None
        merged = self._make_merged_profile(manager)

        merger.preserve_high_confidence(merged, [source], confidence_threshold=0.8)

        assert (merged.preferences or {}).get("directory_specific", {}).get("/docs") == "archive"
        assert (merged.confidence_data or {}).get("/docs") == pytest.approx(0.9)

    def test_high_confidence_learned_pattern_preserved(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        """High-confidence key in learned_patterns is copied (lines 392-399)."""
        _make_profile(
            manager,
            "HighPattern",
            patterns={"preferred_ext": "pdf"},
            confidence={"preferred_ext": 0.88},
        )
        source = manager.get_profile("HighPattern")
        assert source is not None
        merged = self._make_merged_profile(manager)

        merger.preserve_high_confidence(merged, [source], confidence_threshold=0.8)

        assert (merged.learned_patterns or {}).get("preferred_ext") == "pdf"
        assert (merged.confidence_data or {}).get("preferred_ext") == pytest.approx(0.88)

    def test_below_threshold_key_not_preserved(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        """Keys below threshold (lines 359->358) are not preserved."""
        _make_profile(
            manager,
            "LowConf",
            global_prefs={"theme": "light"},
            confidence={"theme": 0.5},
        )
        source = manager.get_profile("LowConf")
        assert source is not None
        merged = self._make_merged_profile(manager)

        merger.preserve_high_confidence(merged, [source], confidence_threshold=0.8)

        # theme confidence is 0.5 < 0.8 threshold — should NOT appear
        assert "theme" not in (merged.preferences or {}).get("global", {})

    def test_higher_confidence_replaces_lower_for_same_key(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        """Second source with higher confidence replaces first (lines 360->358 branch)."""
        _make_profile(
            manager,
            "Src_Low",
            global_prefs={"mode": "basic"},
            confidence={"mode": 0.85},
        )
        _make_profile(
            manager,
            "Src_High",
            global_prefs={"mode": "advanced"},
            confidence={"mode": 0.99},
        )
        src_low = manager.get_profile("Src_Low")
        src_high = manager.get_profile("Src_High")
        assert src_low is not None and src_high is not None
        merged = self._make_merged_profile(manager)

        merger.preserve_high_confidence(merged, [src_low, src_high], confidence_threshold=0.8)

        # src_high has higher confidence, so "advanced" should win
        assert (merged.preferences or {}).get("global", {}).get("mode") == "advanced"
        assert (merged.confidence_data or {}).get("mode") == pytest.approx(0.99)


# ---------------------------------------------------------------------------
# create_merged_profile failure paths (lines 417-435)
# ---------------------------------------------------------------------------


class TestCreateMergedProfile:
    def test_creates_profile_with_merged_data(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        merged_data = {
            "description": "Test merged profile",
            "preferences": {"global": {"key": "val"}, "directory_specific": {}},
            "learned_patterns": {"ext": "pdf"},
            "confidence_data": {"ext": 0.8},
        }
        result = merger.create_merged_profile("new_merged", merged_data)
        assert result is not None
        assert result.profile_name == "new_merged"
        assert (result.preferences or {}).get("global", {}).get("key") == "val"

    def test_returns_none_when_create_profile_fails(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        """Line 418: create_profile returns None → early return None."""
        with patch.object(manager, "create_profile", return_value=None):
            result = merger.create_merged_profile("irrelevant", {})
        assert result is None

    def test_returns_none_when_update_fails_in_create_merged(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        """Line 429: update_profile returns False → return None."""
        with patch.object(manager, "update_profile", return_value=False):
            result = merger.create_merged_profile("wont_update", {"description": "x"})
        assert result is None

    def test_returns_none_on_exception_in_create_merged(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        """Lines 433-435: exception caught → return None."""
        with patch.object(manager, "create_profile", side_effect=OSError("disk full")):
            result = merger.create_merged_profile("fail_me", {})
        assert result is None


# ---------------------------------------------------------------------------
# get_merge_conflicts — exception path (lines 487, 495-497)
# ---------------------------------------------------------------------------


class TestGetMergeConflicts:
    def test_no_conflicts_when_profiles_have_disjoint_keys(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        _make_profile(manager, "ConflA", global_prefs={"a_key": "v1"})
        _make_profile(manager, "ConflB", global_prefs={"b_key": "v2"})

        conflicts = merger.get_merge_conflicts(["ConflA", "ConflB"])
        assert isinstance(conflicts, dict)
        assert "global.a_key" not in conflicts
        assert "global.b_key" not in conflicts

    def test_conflict_detected_for_same_key_different_values(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        _make_profile(manager, "CA", global_prefs={"sort": "asc"})
        _make_profile(manager, "CB", global_prefs={"sort": "desc"})

        conflicts = merger.get_merge_conflicts(["CA", "CB"])
        assert "global.sort" in conflicts
        assert set(conflicts["global.sort"]) == {"asc", "desc"}

    def test_no_conflicts_when_values_are_identical(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        _make_profile(manager, "SA1", global_prefs={"mode": "dark"})
        _make_profile(manager, "SA2", global_prefs={"mode": "dark"})

        conflicts = merger.get_merge_conflicts(["SA1", "SA2"])
        assert "global.mode" not in conflicts

    def test_returns_empty_dict_on_fewer_than_two_found_profiles(
        self, merger: ProfileMerger
    ) -> None:
        """Line 487: fewer than 2 profiles found → returns empty dict early."""
        conflicts = merger.get_merge_conflicts(["ghost_xyz_1", "ghost_xyz_2"])
        assert conflicts == {}

    def test_returns_empty_dict_on_exception(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        """Lines 495-497: exception in get_merge_conflicts → return empty dict."""
        _make_profile(manager, "ExcA", global_prefs={"k": "v"})
        _make_profile(manager, "ExcB", global_prefs={"k": "w"})

        with patch.object(manager, "get_profile", side_effect=KeyError("simulated error")):
            conflicts = merger.get_merge_conflicts(["ExcA", "ExcB"])
        assert conflicts == {}

    def test_dir_specific_conflicts_detected(
        self, manager: ProfileManager, merger: ProfileMerger
    ) -> None:
        _make_profile(manager, "DA", dir_prefs={"/docs": "archive"})
        _make_profile(manager, "DB", dir_prefs={"/docs": "inbox"})

        conflicts = merger.get_merge_conflicts(["DA", "DB"])
        assert "directory_specific./docs" in conflicts
        assert set(conflicts["directory_specific./docs"]) == {"archive", "inbox"}
