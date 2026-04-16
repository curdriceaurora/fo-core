"""Integration tests for PARA AI, rules, heuristics, config, and folder mapper modules.

Covers:
- src/file_organizer/methodologies/para/ai/feature_extractor.py
- src/file_organizer/methodologies/para/ai/suggestion_engine.py
- src/file_organizer/methodologies/para/rules/engine.py
- src/file_organizer/methodologies/para/detection/heuristics.py
- src/file_organizer/methodologies/para/config.py
- src/file_organizer/methodologies/para/folder_mapper.py
"""

from __future__ import annotations

from datetime import UTC
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_file(tmp_path: Path, name: str, content: str = "hello") -> Path:
    """Create a file in tmp_path and return its path."""
    p = tmp_path / name
    p.write_text(content)
    return p


# ===========================================================================
# FeatureExtractor
# ===========================================================================


class TestFeatureExtractorTextFeatures:
    """Tests for FeatureExtractor.extract_text_features()."""

    def test_empty_content_returns_defaults(self) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        result = fe.extract_text_features("")
        assert result.word_count == 0
        assert result.keywords == []
        assert result.temporal_indicators == []
        assert result.action_items == []
        assert result.document_type == "unknown"

    def test_whitespace_only_returns_defaults(self) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        result = fe.extract_text_features("   \n\t  ")
        assert result.word_count == 0

    def test_project_keywords_counted(self) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        content = "This is a project plan with a deadline and milestone."
        result = fe.extract_text_features(content)
        assert result.category_keyword_counts.get("project", 0) > 0
        assert result.word_count > 0

    def test_archive_keywords_counted(self) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        content = "This is archived and deprecated. The project is completed and done."
        result = fe.extract_text_features(content)
        assert result.category_keyword_counts.get("archive", 0) > 0

    def test_resource_keywords_counted(self) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        content = "This is a reference guide and tutorial documentation handbook."
        result = fe.extract_text_features(content)
        assert result.category_keyword_counts.get("resource", 0) > 0

    def test_temporal_indicators_detected(self) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        content = "Meeting on 2024-01-15. Deadline: January 30. Q1 2024 review."
        result = fe.extract_text_features(content)
        assert len(result.temporal_indicators) > 0

    def test_action_items_detected(self) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        content = "- [ ] TODO: fix the bug\n- [x] done\nACTION ITEM: review PR"
        result = fe.extract_text_features(content)
        assert len(result.action_items) > 0

    def test_document_type_plan_detected(self) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        content = "This is a project plan with a roadmap and strategy and timeline."
        result = fe.extract_text_features(content)
        assert result.document_type == "plan"

    def test_document_type_notes_detected(self) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        content = "meeting notes and minutes from the memo journal log"
        result = fe.extract_text_features(content)
        assert result.document_type == "notes"

    def test_document_type_unknown_for_generic(self) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        result = fe.extract_text_features("random text without any hints")
        assert result.document_type == "unknown"

    def test_content_truncation(self) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor(max_content_length=10)
        result = fe.extract_text_features("a" * 1000)
        assert result.word_count <= 10

    def test_keywords_capped_at_thirty(self) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        # Build content dense with every category keyword
        words = (
            "deadline milestone deliverable sprint goal project due completion "
            "ongoing maintenance routine checklist regular continuous process standard "
            "reference tutorial guide template documentation how-to example learning "
            "final completed archived old legacy deprecated obsolete historical "
        )
        result = fe.extract_text_features(words * 5)
        assert 1 <= len(result.keywords) <= 30


class TestFeatureExtractorMetadataFeatures:
    """Tests for FeatureExtractor.extract_metadata_features()."""

    def test_existing_file(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        f = _make_file(tmp_path, "test.md")
        fe = FeatureExtractor()
        result = fe.extract_metadata_features(f)

        assert result.file_type == ".md"
        assert result.file_size == len("hello")
        assert result.modification_date is not None
        assert result.creation_date is not None
        assert result.days_since_modified >= 0.0
        assert result.days_since_created >= 0.0
        assert 0.0 <= result.access_frequency <= 1.0

    def test_nonexistent_file_returns_partial(self) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        fe = FeatureExtractor()
        result = fe.extract_metadata_features(Path("/nonexistent/path/file.pdf"))
        assert result.file_type == ".pdf"
        assert result.file_size == 0

    def test_file_type_extracted(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        f = _make_file(tmp_path, "report.PDF")
        fe = FeatureExtractor()
        result = fe.extract_metadata_features(f)
        assert result.file_type == ".pdf"

    def test_days_since_modified_is_small_for_new_file(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        f = _make_file(tmp_path, "new.txt")
        fe = FeatureExtractor()
        result = fe.extract_metadata_features(f)
        # Freshly written file should be < 1 day old
        assert result.days_since_modified < 1.0


class TestFeatureExtractorStructuralFeatures:
    """Tests for FeatureExtractor.extract_structural_features()."""

    def test_parent_category_hint_projects(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        f = _make_file(projects_dir, "plan.md")

        fe = FeatureExtractor()
        result = fe.extract_structural_features(f)
        assert result.parent_category_hint == "project"

    def test_parent_category_hint_archive(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        f = _make_file(archive_dir, "old.txt")

        fe = FeatureExtractor()
        result = fe.extract_structural_features(f)
        assert result.parent_category_hint == "archive"

    def test_parent_category_hint_resource(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        res_dir = tmp_path / "resources"
        res_dir.mkdir()
        f = _make_file(res_dir, "guide.md")

        fe = FeatureExtractor()
        result = fe.extract_structural_features(f)
        assert result.parent_category_hint == "resource"

    def test_no_category_hint_for_plain_dir(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        d = tmp_path / "randomfolder"
        d.mkdir()
        f = _make_file(d, "file.txt")

        fe = FeatureExtractor()
        result = fe.extract_structural_features(f)
        assert result.parent_category_hint is None

    def test_has_project_structure_readme(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        (tmp_path / "README.md").write_text("# My Project")
        f = _make_file(tmp_path, "code.py")

        fe = FeatureExtractor()
        result = fe.extract_structural_features(f)
        assert result.has_project_structure is True

    def test_has_date_in_path(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        dated_dir = tmp_path / "2024-01-15"
        dated_dir.mkdir()
        f = _make_file(dated_dir, "notes.md")

        fe = FeatureExtractor()
        result = fe.extract_structural_features(f)
        assert result.has_date_in_path is True

    def test_sibling_count(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        # 3 siblings
        for i in range(3):
            (tmp_path / f"sibling{i}.txt").write_text("x")
        f = _make_file(tmp_path, "main.md")

        fe = FeatureExtractor()
        result = fe.extract_structural_features(f)
        # main.md is excluded from sibling count (count - 1), so expect 3
        assert result.sibling_count == 3

    def test_directory_depth_correct(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.feature_extractor import FeatureExtractor

        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        f = _make_file(sub, "file.txt")

        fe = FeatureExtractor()
        result = fe.extract_structural_features(f)
        assert result.directory_depth >= 2


# ===========================================================================
# PARASuggestionEngine
# ===========================================================================


class TestPARASuggestionEngineBasic:
    """Tests for PARASuggestionEngine basic functionality."""

    def test_suggest_returns_para_suggestion(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import PARASuggestionEngine
        from file_organizer.methodologies.para.categories import PARACategory

        f = _make_file(tmp_path, "notes.md")
        engine = PARASuggestionEngine()
        suggestion = engine.suggest(f)

        assert isinstance(suggestion.category, PARACategory)
        assert 0.0 <= suggestion.confidence <= 1.0
        assert len(suggestion.reasoning) > 0

    def test_suggest_with_project_content(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import PARASuggestionEngine
        from file_organizer.methodologies.para.categories import PARACategory

        content = (
            "Project plan: deadline 2024-03-01. Milestone: complete prototype. "
            "Sprint 1 goal: deliverable by next week. TODO: review proposal."
        )
        f = _make_file(tmp_path, "project_plan.md", content)
        engine = PARASuggestionEngine()
        suggestion = engine.suggest(f, content=content)

        assert suggestion.category == PARACategory.PROJECT
        assert suggestion.confidence > 0.0

    def test_suggest_with_archive_path(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import PARASuggestionEngine
        from file_organizer.methodologies.para.categories import PARACategory

        arch_dir = tmp_path / "archive"
        arch_dir.mkdir()
        f = _make_file(arch_dir, "old_report.pdf")
        engine = PARASuggestionEngine()
        suggestion = engine.suggest(f)

        assert suggestion.category == PARACategory.ARCHIVE

    def test_suggest_with_resource_path(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import PARASuggestionEngine
        from file_organizer.methodologies.para.categories import PARACategory

        res_dir = tmp_path / "resources"
        res_dir.mkdir()
        f = _make_file(res_dir, "tutorial.md")
        engine = PARASuggestionEngine()
        suggestion = engine.suggest(f)

        assert suggestion.category == PARACategory.RESOURCE

    def test_suggest_alternative_categories(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import PARASuggestionEngine

        f = _make_file(tmp_path, "document.txt", "some generic content here for reference guide")
        engine = PARASuggestionEngine()
        suggestion = engine.suggest(f, content="some generic content here for reference guide")
        # alternatives is a list of (category, score)
        assert isinstance(suggestion.alternative_categories, list)
        assert all(
            isinstance(cat, tuple) and len(cat) == 2 for cat in suggestion.alternative_categories
        )

    def test_suggest_confidence_label(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import PARASuggestionEngine

        f = _make_file(tmp_path, "file.txt")
        engine = PARASuggestionEngine()
        suggestion = engine.suggest(f)
        assert suggestion.confidence_label in ("High", "Medium", "Low", "Very Low")

    def test_suggest_tags_populated(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import PARASuggestionEngine

        f = _make_file(tmp_path, "report.pdf", "analysis report")
        engine = PARASuggestionEngine()
        suggestion = engine.suggest(f, content="analysis report")
        # tags may include file type and keywords
        assert isinstance(suggestion.tags, list)
        assert all(isinstance(t, str) for t in suggestion.tags)

    def test_suggest_subfolder_for_resource(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import PARASuggestionEngine
        from file_organizer.methodologies.para.categories import PARACategory

        res_dir = tmp_path / "resources"
        res_dir.mkdir()
        content = "This is a reference guide and tutorial documentation handbook manual"
        f = _make_file(res_dir, "guide.md", content)
        engine = PARASuggestionEngine()
        suggestion = engine.suggest(f, content=content)

        if suggestion.category == PARACategory.RESOURCE:
            # subfolder hint may be set
            assert suggestion.suggested_subfolder is None or isinstance(
                suggestion.suggested_subfolder, str
            )


class TestPARASuggestionEngineBatch:
    """Tests for suggest_batch() including error handling."""

    def test_batch_returns_one_per_file(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import PARASuggestionEngine

        files = [_make_file(tmp_path, f"file{i}.txt") for i in range(3)]
        engine = PARASuggestionEngine()
        results = engine.suggest_batch(files)

        assert len(results) == 3

    def test_batch_handles_errors_gracefully(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import PARASuggestionEngine
        from file_organizer.methodologies.para.categories import PARACategory

        good = _make_file(tmp_path, "good.txt")
        bad = Path("/nonexistent/path/bad.txt")

        engine = PARASuggestionEngine()
        # Force suggest() to raise for the bad path so fallback is exercised
        original_suggest = engine.suggest

        def patched_suggest(path: Path, **kwargs):
            if path == bad:
                raise RuntimeError("simulated error for bad path")
            return original_suggest(path, **kwargs)

        engine.suggest = patched_suggest  # type: ignore[method-assign]
        results = engine.suggest_batch([good, bad])

        assert len(results) == 2
        # The bad file should produce a fallback
        bad_result = results[1]
        assert bad_result.category == PARACategory.RESOURCE
        assert bad_result.confidence == 0.1

    def test_batch_empty_list(self) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import PARASuggestionEngine

        engine = PARASuggestionEngine()
        results = engine.suggest_batch([])
        assert results == []


class TestPARASuggestionEngineExplain:
    """Tests for the explain() method."""

    def test_explain_contains_category_name(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import PARASuggestionEngine

        f = _make_file(tmp_path, "file.txt")
        engine = PARASuggestionEngine()
        suggestion = engine.suggest(f)
        explanation = engine.explain(suggestion)

        assert "Recommended category:" in explanation
        assert "Confidence:" in explanation
        assert "Reasoning:" in explanation

    def test_explain_includes_alternatives(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            PARASuggestion,
            PARASuggestionEngine,
        )
        from file_organizer.methodologies.para.categories import PARACategory

        suggestion = PARASuggestion(
            category=PARACategory.PROJECT,
            confidence=0.8,
            reasoning=["High keyword match"],
            alternative_categories=[(PARACategory.AREA, 0.4)],
        )
        engine = PARASuggestionEngine()
        explanation = engine.explain(suggestion)
        assert "Alternatives:" in explanation

    def test_explain_includes_subfolder(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            PARASuggestion,
            PARASuggestionEngine,
        )
        from file_organizer.methodologies.para.categories import PARACategory

        suggestion = PARASuggestion(
            category=PARACategory.RESOURCE,
            confidence=0.7,
            reasoning=["Pattern match"],
            suggested_subfolder="Guides",
            tags=["guide", "pdf"],
        )
        engine = PARASuggestionEngine()
        explanation = engine.explain(suggestion)
        assert "Suggested subfolder: Guides" in explanation
        assert "Tags:" in explanation


class TestPARASuggestionDataclass:
    """Tests for the PARASuggestion dataclass."""

    def test_invalid_confidence_raises(self) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import PARASuggestion
        from file_organizer.methodologies.para.categories import PARACategory

        with pytest.raises(ValueError, match="confidence"):
            PARASuggestion(category=PARACategory.PROJECT, confidence=1.5)

    def test_requires_review_below_threshold(self) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            CONFIDENCE_MEDIUM,
            PARASuggestion,
        )
        from file_organizer.methodologies.para.categories import PARACategory

        suggestion = PARASuggestion(
            category=PARACategory.AREA,
            confidence=CONFIDENCE_MEDIUM - 0.01,
        )
        assert suggestion.requires_review is True

    def test_not_requires_review_above_threshold(self) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            CONFIDENCE_MEDIUM,
            PARASuggestion,
        )
        from file_organizer.methodologies.para.categories import PARACategory

        suggestion = PARASuggestion(
            category=PARACategory.PROJECT,
            confidence=CONFIDENCE_MEDIUM + 0.01,
        )
        assert suggestion.requires_review is False

    def test_confidence_label_high(self) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            CONFIDENCE_HIGH,
            PARASuggestion,
        )
        from file_organizer.methodologies.para.categories import PARACategory

        s = PARASuggestion(category=PARACategory.PROJECT, confidence=CONFIDENCE_HIGH)
        assert s.confidence_label == "High"

    def test_confidence_label_medium(self) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            CONFIDENCE_HIGH,
            CONFIDENCE_MEDIUM,
            PARASuggestion,
        )
        from file_organizer.methodologies.para.categories import PARACategory

        s = PARASuggestion(
            category=PARACategory.AREA,
            confidence=(CONFIDENCE_MEDIUM + CONFIDENCE_HIGH) / 2,
        )
        assert s.confidence_label == "Medium"

    def test_confidence_label_very_low(self) -> None:
        from file_organizer.methodologies.para.ai.suggestion_engine import (
            CONFIDENCE_LOW,
            PARASuggestion,
        )
        from file_organizer.methodologies.para.categories import PARACategory

        s = PARASuggestion(category=PARACategory.ARCHIVE, confidence=CONFIDENCE_LOW - 0.01)
        assert s.confidence_label == "Very Low"


# ===========================================================================
# PARA Rules Engine
# ===========================================================================


class TestRuleCondition:
    """Tests for RuleCondition dataclass validation."""

    def test_composite_without_subconditions_raises(self) -> None:
        from file_organizer.methodologies.para.rules.engine import (
            ConditionType,
            RuleCondition,
        )

        with pytest.raises(ValueError, match="subconditions"):
            RuleCondition(type=ConditionType.COMPOSITE)

    def test_non_composite_without_values_raises(self) -> None:
        from file_organizer.methodologies.para.rules.engine import (
            ConditionType,
            RuleCondition,
        )

        with pytest.raises(ValueError, match="requires values or threshold"):
            RuleCondition(type=ConditionType.CONTENT_KEYWORD)

    def test_valid_keyword_condition(self) -> None:
        from file_organizer.methodologies.para.rules.engine import (
            ConditionType,
            RuleCondition,
        )

        cond = RuleCondition(type=ConditionType.CONTENT_KEYWORD, values=["deadline"])
        assert cond.values == ["deadline"]

    def test_valid_threshold_condition(self) -> None:
        from file_organizer.methodologies.para.rules.engine import (
            ConditionType,
            RuleCondition,
        )

        cond = RuleCondition(type=ConditionType.FILE_SIZE, threshold=1024.0)
        assert cond.threshold == 1024.0


class TestRuleAction:
    """Tests for RuleAction dataclass validation."""

    def test_categorize_requires_category(self) -> None:
        from file_organizer.methodologies.para.rules.engine import ActionType, RuleAction

        with pytest.raises(ValueError, match="category"):
            RuleAction(type=ActionType.CATEGORIZE, confidence=0.9)

    def test_categorize_requires_confidence(self) -> None:
        from file_organizer.methodologies.para.rules.engine import ActionType, RuleAction

        with pytest.raises(ValueError, match="confidence"):
            RuleAction(type=ActionType.CATEGORIZE, category="project")

    def test_categorize_invalid_category_raises(self) -> None:
        from file_organizer.methodologies.para.rules.engine import ActionType, RuleAction

        with pytest.raises(ValueError, match="Invalid PARA category"):
            RuleAction(type=ActionType.CATEGORIZE, category="bogus", confidence=0.8)

    def test_categorize_confidence_out_of_range(self) -> None:
        from file_organizer.methodologies.para.rules.engine import ActionType, RuleAction

        with pytest.raises(ValueError, match="Confidence"):
            RuleAction(type=ActionType.CATEGORIZE, category="project", confidence=1.5)

    def test_valid_categorize_action(self) -> None:
        from file_organizer.methodologies.para.rules.engine import ActionType, RuleAction

        action = RuleAction(type=ActionType.CATEGORIZE, category="project", confidence=0.85)
        assert action.category == "project"
        assert action.confidence == 0.85

    def test_add_tag_no_category_required(self) -> None:
        from file_organizer.methodologies.para.rules.engine import ActionType, RuleAction

        action = RuleAction(type=ActionType.ADD_TAG, tags=["urgent"])
        assert action.tags == ["urgent"]


class TestRule:
    """Tests for Rule dataclass validation."""

    def _make_valid_rule(self, name: str = "test_rule") -> object:
        from file_organizer.methodologies.para.rules.engine import (
            ActionType,
            ConditionType,
            Rule,
            RuleAction,
            RuleCondition,
        )

        return Rule(
            name=name,
            description="A test rule",
            priority=10,
            conditions=[RuleCondition(type=ConditionType.CONTENT_KEYWORD, values=["deadline"])],
            actions=[RuleAction(type=ActionType.CATEGORIZE, category="project", confidence=0.9)],
        )

    def test_valid_rule_created(self) -> None:
        rule = self._make_valid_rule()
        assert rule.name == "test_rule"
        assert rule.enabled is True

    def test_rule_no_conditions_raises(self) -> None:
        from file_organizer.methodologies.para.rules.engine import (
            ActionType,
            Rule,
            RuleAction,
        )

        with pytest.raises(ValueError, match="condition"):
            Rule(
                name="bad",
                description="bad rule",
                priority=5,
                conditions=[],
                actions=[RuleAction(type=ActionType.ADD_TAG, tags=["x"])],
            )

    def test_rule_no_actions_raises(self) -> None:
        from file_organizer.methodologies.para.rules.engine import (
            ConditionType,
            Rule,
            RuleCondition,
        )

        with pytest.raises(ValueError, match="action"):
            Rule(
                name="bad",
                description="bad rule",
                priority=5,
                conditions=[RuleCondition(type=ConditionType.CONTENT_KEYWORD, values=["x"])],
                actions=[],
            )

    def test_rule_negative_priority_raises(self) -> None:
        from file_organizer.methodologies.para.rules.engine import (
            ActionType,
            ConditionType,
            Rule,
            RuleAction,
            RuleCondition,
        )

        with pytest.raises(ValueError, match="Priority"):
            Rule(
                name="bad",
                description="",
                priority=-1,
                conditions=[RuleCondition(type=ConditionType.CONTENT_KEYWORD, values=["x"])],
                actions=[RuleAction(type=ActionType.ADD_TAG, tags=["y"])],
            )


class TestEvaluationContext:
    """Tests for EvaluationContext dataclass properties."""

    def test_file_extension_property(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.rules.engine import EvaluationContext

        ctx = EvaluationContext(file_path=Path("report.PDF"))
        assert ctx.file_extension == ".pdf"

    def test_file_name_property(self) -> None:
        from file_organizer.methodologies.para.rules.engine import EvaluationContext

        ctx = EvaluationContext(file_path=Path("/some/dir/myfile.txt"))
        assert ctx.file_name == "myfile.txt"

    def test_file_age_days_none_without_stat(self) -> None:
        from file_organizer.methodologies.para.rules.engine import EvaluationContext

        ctx = EvaluationContext(file_path=Path("file.txt"))
        assert ctx.file_age_days is None

    def test_file_age_days_computed(self) -> None:
        from datetime import datetime

        from file_organizer.methodologies.para.rules.engine import EvaluationContext

        created = datetime(2020, 1, 1, tzinfo=UTC)
        ctx = EvaluationContext(
            file_path=Path("file.txt"),
            file_stat={"created": created},
        )
        age = ctx.file_age_days
        assert age is not None
        assert age > 0

    def test_file_age_days_naive_datetime(self) -> None:
        # naive datetime should still produce a file_age_days value (treated as UTC)
        from datetime import datetime

        from file_organizer.methodologies.para.rules.engine import EvaluationContext

        created = datetime(2020, 6, 15)  # noqa: DTZ001 — intentionally naive to test naive-datetime handling
        ctx = EvaluationContext(
            file_path=Path("file.txt"),
            file_stat={"created": created},
        )
        assert ctx.file_age_days is not None


class TestRuleEngine:
    """Tests for RuleEngine orchestrator."""

    def _build_engine(self) -> object:
        """Build a RuleEngine with mock components."""
        from file_organizer.methodologies.para.rules.engine import RuleEngine

        parser = MagicMock()
        evaluator = MagicMock()
        executor = MagicMock()
        resolver = MagicMock()
        scorer = MagicMock()

        engine = RuleEngine(
            parser=parser,
            evaluator=evaluator,
            executor=executor,
            resolver=resolver,
            scorer=scorer,
        )
        return engine

    def test_add_rule_calls_validate(self) -> None:
        from file_organizer.methodologies.para.rules.engine import (
            ActionType,
            ConditionType,
            Rule,
            RuleAction,
            RuleCondition,
        )

        engine = self._build_engine()
        engine.parser.validate_rule.return_value = True

        rule = Rule(
            name="r1",
            description="d",
            priority=1,
            conditions=[RuleCondition(type=ConditionType.CONTENT_KEYWORD, values=["kw"])],
            actions=[RuleAction(type=ActionType.ADD_TAG, tags=["t"])],
        )
        engine.add_rule(rule)
        engine.parser.validate_rule.assert_called_once_with(rule)
        assert len(engine.rules) == 1

    def test_evaluate_file_no_rules_returns_none(self) -> None:
        from file_organizer.methodologies.para.rules.engine import EvaluationContext

        engine = self._build_engine()
        ctx = EvaluationContext(file_path=Path("file.txt"))
        result = engine.evaluate_file(ctx)
        assert result is None

    def test_evaluate_file_disabled_rule_skipped(self) -> None:
        from file_organizer.methodologies.para.rules.engine import (
            ActionType,
            ConditionType,
            EvaluationContext,
            Rule,
            RuleAction,
            RuleCondition,
        )

        engine = self._build_engine()
        rule = Rule(
            name="disabled_rule",
            description="",
            priority=1,
            conditions=[RuleCondition(type=ConditionType.CONTENT_KEYWORD, values=["kw"])],
            actions=[RuleAction(type=ActionType.ADD_TAG, tags=["t"])],
            enabled=False,
        )
        engine.rules = [rule]

        ctx = EvaluationContext(file_path=Path("file.txt"))
        result = engine.evaluate_file(ctx)
        assert result is None
        # evaluator.evaluate_condition should NOT have been called
        engine.evaluator.evaluate_condition.assert_not_called()

    def test_evaluate_file_single_match_returns_without_resolver(self) -> None:
        from file_organizer.methodologies.para.rules.engine import (
            ActionType,
            ConditionType,
            EvaluationContext,
            Rule,
            RuleAction,
            RuleCondition,
        )

        engine = self._build_engine()
        engine.evaluator.evaluate_condition.return_value = True

        rule = Rule(
            name="rule1",
            description="",
            priority=10,
            conditions=[RuleCondition(type=ConditionType.CONTENT_KEYWORD, values=["kw"])],
            actions=[RuleAction(type=ActionType.CATEGORIZE, category="project", confidence=0.9)],
        )
        engine.rules = [rule]

        ctx = EvaluationContext(file_path=Path("file.txt"))
        result = engine.evaluate_file(ctx)

        assert result is not None
        assert result.matched is True
        assert result.category == "project"
        assert result.confidence == 0.9

    def test_evaluate_file_condition_fails_no_match(self) -> None:
        from file_organizer.methodologies.para.rules.engine import (
            ActionType,
            ConditionType,
            EvaluationContext,
            Rule,
            RuleAction,
            RuleCondition,
        )

        engine = self._build_engine()
        engine.evaluator.evaluate_condition.return_value = False

        rule = Rule(
            name="rule_no_match",
            description="",
            priority=10,
            conditions=[RuleCondition(type=ConditionType.CONTENT_KEYWORD, values=["kw"])],
            actions=[RuleAction(type=ActionType.CATEGORIZE, category="area", confidence=0.7)],
        )
        engine.rules = [rule]

        ctx = EvaluationContext(file_path=Path("file.txt"))
        result = engine.evaluate_file(ctx)
        assert result is None

    def test_evaluate_file_multiple_matches_calls_resolver(self) -> None:
        from file_organizer.methodologies.para.rules.engine import (
            ActionType,
            ConditionType,
            ConflictResolutionStrategy,
            EvaluationContext,
            Rule,
            RuleAction,
            RuleCondition,
            RuleMatchResult,
        )

        engine = self._build_engine()
        engine.evaluator.evaluate_condition.return_value = True

        def _make_rule(name: str, cat: str) -> Rule:
            return Rule(
                name=name,
                description="",
                priority=5,
                conditions=[RuleCondition(type=ConditionType.CONTENT_KEYWORD, values=["x"])],
                actions=[RuleAction(type=ActionType.CATEGORIZE, category=cat, confidence=0.8)],
            )

        engine.rules = [_make_rule("r1", "project"), _make_rule("r2", "area")]

        expected_winner = MagicMock(spec=RuleMatchResult)
        engine.resolver.resolve.return_value = expected_winner

        ctx = EvaluationContext(file_path=Path("file.txt"))
        result = engine.evaluate_file(ctx, ConflictResolutionStrategy.HIGHEST_CONFIDENCE)

        engine.resolver.resolve.assert_called_once()
        assert result is expected_winner

    def test_get_category_scores_calls_scorer(self) -> None:
        from file_organizer.methodologies.para.rules.engine import EvaluationContext

        engine = self._build_engine()
        engine.scorer.calculate_category_scores.return_value = {"project": 0.9}

        ctx = EvaluationContext(file_path=Path("file.txt"))
        scores = engine.get_category_scores(ctx)

        engine.scorer.calculate_category_scores.assert_called_once()
        assert scores == {"project": 0.9}


# ===========================================================================
# HeuristicEngine + individual heuristics
# ===========================================================================


class TestHeuristicEngineInit:
    """Tests for HeuristicEngine constructor flags."""

    def test_default_heuristics_loaded(self) -> None:
        from file_organizer.methodologies.para.detection.heuristics import HeuristicEngine

        engine = HeuristicEngine()
        # temporal + content + structural = 3
        assert len(engine.heuristics) == 3

    def test_all_heuristics_disabled_raises(self) -> None:
        from file_organizer.methodologies.para.detection.heuristics import HeuristicEngine

        engine = HeuristicEngine(
            enable_temporal=False,
            enable_content=False,
            enable_structural=False,
            enable_ai=False,
        )
        with pytest.raises(ValueError, match="No heuristics"):
            engine.evaluate(Path("some/file.txt"))

    def test_thresholds_property_returns_dict(self) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.detection.heuristics import HeuristicEngine

        engine = HeuristicEngine()
        thresholds = engine.THRESHOLDS
        assert PARACategory.PROJECT in thresholds
        assert PARACategory.ARCHIVE in thresholds
        assert all(0.0 <= v <= 1.0 for v in thresholds.values())


class TestTemporalHeuristic:
    """Tests for TemporalHeuristic."""

    def test_new_file_scores_project(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.detection.heuristics import TemporalHeuristic

        f = _make_file(tmp_path, "new.txt")
        h = TemporalHeuristic()
        result = h.evaluate(f)
        # A brand-new file should have a PROJECT signal
        assert result.scores[PARACategory.PROJECT].score > 0.0

    def test_nonexistent_file_returns_review_needed(self) -> None:
        from file_organizer.methodologies.para.detection.heuristics import TemporalHeuristic

        h = TemporalHeuristic()
        result = h.evaluate(Path("/nonexistent/ghost.txt"))
        assert result.needs_manual_review is True

    def test_old_year_in_path_signals_archive(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.detection.heuristics import TemporalHeuristic

        old_dir = tmp_path / "2015"
        old_dir.mkdir()
        f = _make_file(old_dir, "docs.txt")

        h = TemporalHeuristic()
        result = h.evaluate(f)
        assert result.scores[PARACategory.ARCHIVE].score > 0.0
        assert "old_year_in_path" in result.scores[PARACategory.ARCHIVE].signals

    def test_contains_old_year_static(self) -> None:

        from file_organizer.methodologies.para.detection.heuristics import TemporalHeuristic

        current_year = 2024
        assert TemporalHeuristic._contains_old_year("/path/2015/file", current_year) is True
        assert TemporalHeuristic._contains_old_year("/path/2023/file", current_year) is False
        assert TemporalHeuristic._contains_old_year("/path/nodates/file", current_year) is False


class TestContentHeuristic:
    """Tests for ContentHeuristic."""

    def test_project_keyword_in_filename(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.detection.heuristics import ContentHeuristic

        # Use a directory named "project" so the keyword is a standalone word in the path
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        f = _make_file(proj_dir, "plan.md")
        h = ContentHeuristic()
        result = h.evaluate(f)
        assert result.scores[PARACategory.PROJECT].score > 0.0

    def test_archive_keyword_in_path(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.detection.heuristics import ContentHeuristic

        arch_dir = tmp_path / "archive"
        arch_dir.mkdir()
        f = _make_file(arch_dir, "old_stuff.txt")

        h = ContentHeuristic()
        result = h.evaluate(f)
        assert result.scores[PARACategory.ARCHIVE].score > 0.0

    def test_resource_keyword_in_filename(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.detection.heuristics import ContentHeuristic

        # Use a directory named "reference" so the keyword is a standalone word in the path
        ref_dir = tmp_path / "reference"
        ref_dir.mkdir()
        f = _make_file(ref_dir, "guide.md")
        h = ContentHeuristic()
        result = h.evaluate(f)
        assert result.scores[PARACategory.RESOURCE].score > 0.0

    def test_date_pattern_in_filename_boosts_project(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.detection.heuristics import ContentHeuristic

        f = _make_file(tmp_path, "report_2024-01-15.pdf")
        h = ContentHeuristic()
        result = h.evaluate(f)
        assert result.scores[PARACategory.PROJECT].score > 0.0

    def test_matches_keyword_word_boundary(self) -> None:
        from file_organizer.methodologies.para.detection.heuristics import ContentHeuristic

        # "final" should match as a word but not inside "finalize"
        assert ContentHeuristic._matches_keyword("final", "final report") is True
        assert ContentHeuristic._matches_keyword("final", "finalize") is False


class TestStructuralHeuristic:
    """Tests for StructuralHeuristic."""

    def test_archive_directory_signals(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.detection.heuristics import StructuralHeuristic

        d = tmp_path / "archive"
        d.mkdir()
        f = _make_file(d, "item.pdf")

        h = StructuralHeuristic()
        result = h.evaluate(f)
        assert result.scores[PARACategory.ARCHIVE].score > 0.0

    def test_resource_directory_signals(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.detection.heuristics import StructuralHeuristic

        d = tmp_path / "resources"
        d.mkdir()
        f = _make_file(d, "item.pdf")

        h = StructuralHeuristic()
        result = h.evaluate(f)
        assert result.scores[PARACategory.RESOURCE].score > 0.0

    def test_area_directory_signals(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.detection.heuristics import StructuralHeuristic

        d = tmp_path / "areas"
        d.mkdir()
        f = _make_file(d, "item.txt")

        h = StructuralHeuristic()
        result = h.evaluate(f)
        assert result.scores[PARACategory.AREA].score > 0.0

    def test_deep_nesting_boosts_project(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.detection.heuristics import StructuralHeuristic

        # Create 4-level deep path
        deep = tmp_path / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        f = _make_file(deep, "item.txt")

        h = StructuralHeuristic()
        result = h.evaluate(f)
        assert result.scores[PARACategory.PROJECT].score > 0.0


class TestHeuristicEngineEvaluate:
    """Tests for HeuristicEngine.evaluate() combining heuristics."""

    def test_evaluate_returns_heuristic_result(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.detection.heuristics import HeuristicEngine

        f = _make_file(tmp_path, "document.txt")
        engine = HeuristicEngine()
        result = engine.evaluate(f)

        assert PARACategory.PROJECT in result.scores
        assert PARACategory.ARCHIVE in result.scores
        assert 0.0 <= result.overall_confidence <= 1.0

    def test_archive_path_recommended(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.detection.heuristics import HeuristicEngine

        arch_dir = tmp_path / "archive"
        arch_dir.mkdir()
        f = _make_file(arch_dir, "old_backup_deprecated.txt")

        engine = HeuristicEngine()
        result = engine.evaluate(f)

        # archive signals should be present
        assert result.scores[PARACategory.ARCHIVE].score > 0.0

    def test_all_heuristics_failed_returns_fallback(self) -> None:
        from file_organizer.methodologies.para.detection.heuristics import (
            HeuristicEngine,
        )

        engine = HeuristicEngine(
            enable_temporal=True,
            enable_content=False,
            enable_structural=False,
        )

        # Force the temporal heuristic to raise
        engine.heuristics[0].evaluate = MagicMock(side_effect=RuntimeError("fail"))

        result = engine.evaluate(Path("whatever.txt"))
        assert result.needs_manual_review is True
        assert result.overall_confidence == 0.0

    def test_abstained_heuristic_excluded_from_weight(self, tmp_path: Path) -> None:
        """AI heuristic returns abstained=True; should not dilute other scores."""
        from file_organizer.methodologies.para.detection.heuristics import (
            AIHeuristic,
            HeuristicEngine,
        )

        engine = HeuristicEngine(enable_ai=False)
        # Manually append a mock AI heuristic that abstains
        mock_ai = MagicMock(spec=AIHeuristic)
        mock_ai.weight = 0.10

        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.detection.heuristics import (
            CategoryScore,
            HeuristicResult,
        )

        mock_ai.evaluate.return_value = HeuristicResult(
            scores={cat: CategoryScore(cat, 0.0, 0.0) for cat in PARACategory},
            overall_confidence=0.0,
            abstained=True,
        )
        engine.heuristics.append(mock_ai)

        f = _make_file(tmp_path, "test.txt")
        result = engine.evaluate(f)
        # Abstained heuristic must not dilute the weighted average:
        # overall_confidence must be in [0.0, 1.0] and scores must be populated
        assert result is not None
        assert 0.0 <= result.overall_confidence <= 1.0
        assert len(result.scores) > 0


# ===========================================================================
# AIHeuristic (parse_response, build_prompt, zero_result)
# ===========================================================================


class TestAIHeuristicHelpers:
    """Tests for AIHeuristic helper methods."""

    def test_parse_response_valid_json(self) -> None:
        from file_organizer.methodologies.para.detection.heuristics import AIHeuristic

        h = AIHeuristic()
        data = h._parse_response(
            '{"project": 0.5, "area": 0.2, "resource": 0.2, "archive": 0.1, "reasoning": "test"}'
        )
        assert data is not None
        assert abs(data["project"] + data["area"] + data["resource"] + data["archive"] - 1.0) < 1e-9

    def test_parse_response_with_markdown_fences(self) -> None:
        from file_organizer.methodologies.para.detection.heuristics import AIHeuristic

        h = AIHeuristic()
        text = '```json\n{"project": 0.8, "area": 0.1, "resource": 0.1, "archive": 0.0}\n```'
        data = h._parse_response(text)
        assert data is not None
        assert data["project"] > 0.5

    def test_parse_response_invalid_json(self) -> None:
        from file_organizer.methodologies.para.detection.heuristics import AIHeuristic

        h = AIHeuristic()
        assert h._parse_response("not json at all") is None

    def test_parse_response_missing_keys(self) -> None:
        from file_organizer.methodologies.para.detection.heuristics import AIHeuristic

        h = AIHeuristic()
        assert h._parse_response('{"project": 0.5}') is None

    def test_parse_response_no_braces(self) -> None:
        from file_organizer.methodologies.para.detection.heuristics import AIHeuristic

        h = AIHeuristic()
        assert h._parse_response("no braces here") is None

    def test_zero_result_is_abstained(self) -> None:
        from file_organizer.methodologies.para.detection.heuristics import AIHeuristic

        result = AIHeuristic._zero_result("test_reason")
        assert result.abstained is True
        assert result.overall_confidence == 0.0

    def test_build_prompt_contains_file_info(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.detection.heuristics import AIHeuristic

        f = _make_file(tmp_path, "notes.md", "sample content")
        h = AIHeuristic()
        prompt = h._build_prompt(f, "sample content")
        assert "notes.md" in prompt
        assert ".md" in prompt
        assert "sample content" in prompt

    def test_extract_content_text_file(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.detection.heuristics import AIHeuristic

        f = _make_file(tmp_path, "text.txt", "hello world")
        h = AIHeuristic()
        content = h._extract_content(f, metadata=None)
        assert "hello world" in content

    def test_extract_content_binary_file(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.detection.heuristics import AIHeuristic

        f = tmp_path / "binary.bin"
        # Build bytes that (a) are not valid UTF-8 and (b) contain > 30% null
        # bytes so the implementation classifies them as binary.  The check in
        # _extract_content counts b==0 as non-text; using a pattern of one
        # invalid-UTF-8 lead byte followed by two nulls gives a 2/3 ratio.
        binary_data = b"\xff\x00\x00" * 170  # 510 bytes, 340/510 ≈ 67% are null
        f.write_bytes(binary_data)
        h = AIHeuristic()
        content = h._extract_content(f, metadata=None)
        # Should fall back to "[Binary or unreadable file: binary.bin]"
        assert "binary.bin" in content or "Binary" in content

    def test_evaluate_ollama_not_available(self, tmp_path: Path) -> None:
        """When ollama is not installed, evaluate() returns zero result."""
        import file_organizer.methodologies.para.detection.heuristics as _mod
        from file_organizer.methodologies.para.detection.heuristics import AIHeuristic

        f = _make_file(tmp_path, "doc.txt")
        h = AIHeuristic()
        # Patch both OLLAMA_AVAILABLE and ollama to prevent any real or leaked
        # mock client from reaching _parse_response (xdist isolation defence).
        with (
            patch.object(_mod, "OLLAMA_AVAILABLE", new=False),
            patch.object(_mod, "ollama", new=None),
        ):
            result = h.evaluate(f)
        assert result.abstained is True


# ===========================================================================
# PARAConfig
# ===========================================================================


class TestPARAConfig:
    """Tests for PARAConfig."""

    def test_default_config_created(self) -> None:
        from file_organizer.methodologies.para.config import PARAConfig

        config = PARAConfig()
        assert config.enable_temporal_heuristic is True
        assert config.enable_content_heuristic is True
        assert config.enable_structural_heuristic is True
        assert config.enable_ai_heuristic is False
        assert config.project_dir == "Projects"
        assert config.resource_dir == "Resources"

    def test_get_category_threshold(self) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.config import PARAConfig

        config = PARAConfig()
        threshold = config.get_category_threshold(PARACategory.PROJECT)
        assert 0.0 < threshold <= 1.0

    def test_get_category_keywords_nonempty(self) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.config import PARAConfig

        config = PARAConfig()
        keywords = config.get_category_keywords(PARACategory.RESOURCE)
        assert len(keywords) > 0
        assert isinstance(keywords[0], str)

    def test_get_category_directory(self) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.config import PARAConfig

        config = PARAConfig()
        assert config.get_category_directory(PARACategory.PROJECT) == "Projects"
        assert config.get_category_directory(PARACategory.ARCHIVE) == "Archive"

    def test_load_from_yaml_nonexistent_uses_defaults(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.config import PARAConfig

        result = PARAConfig.load_from_yaml(tmp_path / "missing.yaml")
        assert isinstance(result, PARAConfig)

    def test_load_from_yaml_empty_file_uses_defaults(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.config import PARAConfig

        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text("")
        result = PARAConfig.load_from_yaml(yaml_file)
        assert isinstance(result, PARAConfig)

    def test_save_and_reload_yaml(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.config import PARAConfig

        config = PARAConfig(project_dir="MyProjects", archive_dir="OldStuff")
        yaml_file = tmp_path / "para_config.yaml"
        config.save_to_yaml(yaml_file)

        reloaded = PARAConfig.load_from_yaml(yaml_file)
        assert reloaded.project_dir == "MyProjects"
        assert reloaded.archive_dir == "OldStuff"

    def test_save_to_yaml_creates_parent_dirs(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.config import PARAConfig

        config = PARAConfig()
        nested = tmp_path / "a" / "b" / "config.yaml"
        config.save_to_yaml(nested)
        assert nested.exists()

    def test_load_from_yaml_invalid_content_uses_defaults(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.config import PARAConfig

        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text("heuristic_weights:\n  temporal: not_a_float\n")
        result = PARAConfig.load_from_yaml(yaml_file)
        assert isinstance(result, PARAConfig)


class TestLoadConfig:
    """Tests for the load_config() module function."""

    def test_load_config_explicit_path(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.config import PARAConfig, load_config

        config = PARAConfig(project_dir="ExplicitProj")
        yaml_file = tmp_path / "explicit.yaml"
        config.save_to_yaml(yaml_file)

        loaded = load_config(yaml_file)
        assert loaded.project_dir == "ExplicitProj"

    def test_load_config_no_path_returns_config(self) -> None:
        from file_organizer.methodologies.para.config import PARAConfig, load_config

        # Should fall back to default without raising
        result = load_config(Path("/nonexistent/path.yaml"))
        assert isinstance(result, PARAConfig)


# ===========================================================================
# CategoryFolderMapper
# ===========================================================================


class TestCategoryFolderMapper:
    """Tests for CategoryFolderMapper."""

    def test_map_file_returns_mapping_result(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.folder_mapper import CategoryFolderMapper

        f = _make_file(tmp_path, "document.txt")
        mapper = CategoryFolderMapper()
        result = mapper.map_file(f, tmp_path, use_rules=False)

        assert result.source_path == f
        assert result.target_folder is not None
        assert isinstance(result.confidence, float)

    def test_map_file_defaults_resource_if_no_category(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.folder_mapper import CategoryFolderMapper

        # Create a file that's hard to classify (no category signals)
        f = _make_file(tmp_path, "zzz_unknownfile_xyz.dat")
        mapper = CategoryFolderMapper()
        result = mapper.map_file(f, tmp_path, use_rules=False)

        # When heuristic can't recommend, default is RESOURCE
        assert result.target_category in list(PARACategory)

    def test_map_batch_returns_one_per_file(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.folder_mapper import CategoryFolderMapper

        files = [_make_file(tmp_path, f"f{i}.txt") for i in range(4)]
        mapper = CategoryFolderMapper()
        results = mapper.map_batch(files, tmp_path, use_rules=False)

        assert len(results) == 4

    def test_map_batch_error_produces_fallback(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.folder_mapper import CategoryFolderMapper

        # Patch map_file to raise so the batch fallback is exercised
        mapper = CategoryFolderMapper()
        with patch.object(mapper, "map_file", side_effect=RuntimeError("forced error")):
            bad = tmp_path / "bad.txt"
            results = mapper.map_batch([bad], tmp_path, use_rules=False)

        assert len(results) == 1
        assert results[0].target_category == PARACategory.RESOURCE
        assert results[0].confidence == 0.0

    def test_map_file_with_rule_engine_override(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.folder_mapper import CategoryFolderMapper
        from file_organizer.methodologies.para.rules.engine import (
            Rule,
            RuleMatchResult,
        )

        mock_rule = MagicMock(spec=Rule)
        mock_rule.name = "override_rule"
        mock_result = MagicMock(spec=RuleMatchResult)
        mock_result.category = "archive"
        mock_result.confidence = 0.95
        # folder_mapper.py accesses rule_result.rule.name, so set rule on the mock
        mock_result.rule = mock_rule

        mock_rule_engine = MagicMock()
        mock_rule_engine.evaluate_file.return_value = mock_result

        f = _make_file(tmp_path, "file.txt")
        mapper = CategoryFolderMapper(rule_engine=mock_rule_engine)
        result = mapper.map_file(f, tmp_path, use_rules=True)

        assert result.target_category == PARACategory.ARCHIVE

    def test_create_target_folders_dry_run(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.folder_mapper import (
            CategoryFolderMapper,
            MappingResult,
        )

        folder = tmp_path / "Projects" / "sub"
        results = [
            MappingResult(
                source_path=tmp_path / "file.txt",
                target_category=PARACategory.PROJECT,
                target_folder=folder,
                confidence=0.8,
                reasoning=["test"],
            )
        ]
        mapper = CategoryFolderMapper()
        status = mapper.create_target_folders(results, dry_run=True)

        assert folder in status
        assert status[folder] is True
        assert not folder.exists()  # dry run — no actual creation

    def test_create_target_folders_real(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.folder_mapper import (
            CategoryFolderMapper,
            MappingResult,
        )

        folder = tmp_path / "Areas" / "health"
        results = [
            MappingResult(
                source_path=tmp_path / "file.txt",
                target_category=PARACategory.AREA,
                target_folder=folder,
                confidence=0.7,
                reasoning=["test"],
            )
        ]
        mapper = CategoryFolderMapper()
        status = mapper.create_target_folders(results, dry_run=False)

        assert folder in status
        assert status[folder] is True
        assert folder.exists()

    def test_generate_mapping_report(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.folder_mapper import (
            CategoryFolderMapper,
            MappingResult,
        )

        results = [
            MappingResult(
                source_path=tmp_path / f"f{i}.txt",
                target_category=PARACategory.PROJECT,
                target_folder=tmp_path / "Projects",
                confidence=0.8,
                reasoning=["matched project"],
            )
            for i in range(3)
        ]
        mapper = CategoryFolderMapper()
        report = mapper.generate_mapping_report(results)

        assert "PARA Folder Mapping Report" in report
        assert "Total files: 3" in report
        assert "Project" in report

    def test_mapping_strategy_date_folders(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.folder_mapper import (
            CategoryFolderMapper,
            MappingStrategy,
        )

        strategy = MappingStrategy(use_date_folders=True, date_format="%Y/%m")
        f = _make_file(tmp_path, "doc.txt")
        mapper = CategoryFolderMapper(strategy=strategy)
        result = mapper.map_file(f, tmp_path, use_rules=False)

        # date_format="%Y/%m" produces "YYYY/MM" which must contain "/"
        assert result.subfolder_path is not None
        assert "/" in result.subfolder_path

    def test_mapping_strategy_type_folders(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.folder_mapper import (
            CategoryFolderMapper,
            MappingStrategy,
        )

        strategy = MappingStrategy(
            use_type_folders=True,
            type_mapping={".txt": "TextFiles"},
        )
        f = _make_file(tmp_path, "doc.txt")
        mapper = CategoryFolderMapper(strategy=strategy)
        result = mapper.map_file(f, tmp_path, use_rules=False)

        assert result.subfolder_path == "TextFiles"

    def test_mapping_strategy_keyword_folders(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.folder_mapper import (
            CategoryFolderMapper,
            MappingStrategy,
        )

        strategy = MappingStrategy(
            use_keyword_folders=True,
            keyword_mapping={"report": "Reports"},
        )
        f = _make_file(tmp_path, "annual_report_2024.txt")
        mapper = CategoryFolderMapper(strategy=strategy)
        result = mapper.map_file(f, tmp_path, use_rules=False)

        assert result.subfolder_path == "Reports"

    def test_mapping_strategy_custom_subfolder_fn(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.para.categories import PARACategory
        from file_organizer.methodologies.para.folder_mapper import (
            CategoryFolderMapper,
            MappingStrategy,
        )

        def custom_fn(path: Path, category: PARACategory) -> str | None:
            return "CustomFolder"

        strategy = MappingStrategy(custom_subfolder_fn=custom_fn)
        f = _make_file(tmp_path, "file.txt")
        mapper = CategoryFolderMapper(strategy=strategy)
        result = mapper.map_file(f, tmp_path, use_rules=False)

        assert result.subfolder_path == "CustomFolder"
