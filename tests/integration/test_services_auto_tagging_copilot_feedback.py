"""Integration tests for auto-tagging, copilot, and suggestion-feedback modules.

Covers:
  - services/auto_tagging/__init__.py — AutoTaggingService
  - services/auto_tagging/tag_learning.py — TagLearningEngine, TagPattern, TagUsage
  - services/auto_tagging/tag_recommender.py — TagRecommender, TagRecommendation
  - services/copilot/rules/preview.py — PreviewEngine, PreviewResult, FileMatch
  - services/copilot/executor.py — CommandExecutor (additional branches)
  - services/suggestion_feedback.py — SuggestionFeedback, FeedbackEntry, LearningStats
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# TagLearningEngine
# ---------------------------------------------------------------------------


class TestTagLearningEngine:
    def test_record_tag_application_basic(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_learning import TagLearningEngine

        engine = TagLearningEngine(storage_path=tmp_path / "tags.json")
        f = tmp_path / "doc.pdf"
        f.touch()
        engine.record_tag_application(f, ["work", "finance"])
        assert "work" in engine.tag_usage
        assert engine.tag_usage["work"].count == 1

    def test_record_tag_application_empty_tags_skipped(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_learning import TagLearningEngine

        engine = TagLearningEngine(storage_path=tmp_path / "tags.json")
        f = tmp_path / "doc.pdf"
        f.touch()
        engine.record_tag_application(f, [])
        assert len(engine.tag_usage) == 0

    def test_record_cooccurrence(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_learning import TagLearningEngine

        engine = TagLearningEngine(storage_path=tmp_path / "tags.json")
        f = tmp_path / "doc.pdf"
        f.touch()
        engine.record_tag_application(f, ["a", "b", "c"])
        assert engine.tag_cooccurrence["a"]["b"] == 1
        assert engine.tag_cooccurrence["b"]["a"] == 1
        assert engine.tag_cooccurrence["a"]["c"] == 1

    def test_predict_tags_from_file_type(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_learning import TagLearningEngine

        engine = TagLearningEngine(storage_path=tmp_path / "tags.json")
        pdf1 = tmp_path / "r1.pdf"
        pdf2 = tmp_path / "r2.pdf"
        pdf1.touch()
        pdf2.touch()
        for _ in range(5):
            engine.record_tag_application(pdf1, ["report"])
        predictions = engine.predict_tags(pdf2)
        assert any(tag == "report" for tag, _ in predictions)

    def test_predict_tags_from_directory(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_learning import TagLearningEngine

        engine = TagLearningEngine(storage_path=tmp_path / "tags.json")
        subdir = tmp_path / "projects"
        subdir.mkdir()
        existing = subdir / "old.txt"
        existing.touch()
        for _ in range(5):
            engine.record_tag_application(existing, ["project"])
        new_file = subdir / "new.txt"
        new_file.touch()
        predictions = engine.predict_tags(new_file)
        tags = [t for t, _ in predictions]
        assert "project" in tags

    def test_get_related_tags(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_learning import TagLearningEngine

        engine = TagLearningEngine(storage_path=tmp_path / "tags.json")
        f = tmp_path / "f.txt"
        f.touch()
        for _ in range(3):
            engine.record_tag_application(f, ["alpha", "beta", "gamma"])
        related = engine.get_related_tags("alpha")
        assert "beta" in related
        assert "gamma" in related

    def test_get_related_tags_unknown_returns_empty(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_learning import TagLearningEngine

        engine = TagLearningEngine(storage_path=tmp_path / "tags.json")
        assert engine.get_related_tags("unknown") == []

    def test_update_model_accepted_tags(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_learning import TagLearningEngine

        engine = TagLearningEngine(storage_path=tmp_path / "tags.json")
        f = tmp_path / "model.txt"
        f.touch()
        feedback = [
            {
                "file_path": str(f),
                "accepted_tags": ["accepted_tag"],
                "rejected_tags": [],
            }
        ]
        engine.update_model(feedback)
        assert "accepted_tag" in engine.tag_usage

    def test_update_model_rejected_tags_decrease_count(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_learning import TagLearningEngine

        engine = TagLearningEngine(storage_path=tmp_path / "tags.json")
        f = tmp_path / "model2.txt"
        f.touch()
        engine.record_tag_application(f, ["rej_tag"])
        engine.record_tag_application(f, ["rej_tag"])
        engine.record_tag_application(f, ["rej_tag"])
        initial_count = engine.tag_usage["rej_tag"].count

        feedback = [
            {
                "file_path": str(f),
                "accepted_tags": [],
                "rejected_tags": ["rej_tag"],
            }
        ]
        engine.update_model(feedback)
        assert engine.tag_usage["rej_tag"].count < initial_count

    def test_get_popular_tags(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_learning import TagLearningEngine

        engine = TagLearningEngine(storage_path=tmp_path / "tags.json")
        f = tmp_path / "popular.txt"
        f.touch()
        for _ in range(10):
            engine.record_tag_application(f, ["popular"])
        for _ in range(2):
            engine.record_tag_application(f, ["rare"])

        popular = engine.get_popular_tags(limit=5)
        assert popular[0][0] == "popular"
        assert popular[0][1] == 10

    def test_get_recent_tags(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_learning import TagLearningEngine

        engine = TagLearningEngine(storage_path=tmp_path / "tags.json")
        f = tmp_path / "recent.txt"
        f.touch()
        engine.record_tag_application(f, ["new_tag"])
        recent = engine.get_recent_tags(days=30)
        assert "new_tag" in recent

    def test_get_recent_tags_excludes_old(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_learning import TagLearningEngine, TagUsage

        engine = TagLearningEngine(storage_path=tmp_path / "tags.json")
        old_usage = TagUsage(
            tag="old_tag",
            count=5,
            last_used=datetime.now(UTC) - timedelta(days=100),
        )
        engine.tag_usage["old_tag"] = old_usage
        recent = engine.get_recent_tags(days=30)
        assert "old_tag" not in recent

    def test_get_tag_suggestions_for_context_by_type(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_learning import TagLearningEngine

        engine = TagLearningEngine(storage_path=tmp_path / "tags.json")
        f = tmp_path / "ctx.py"
        f.touch()
        for _ in range(3):
            engine.record_tag_application(f, ["python", "code"])

        suggestions = engine.get_tag_suggestions_for_context(file_type=".py")
        tags = [t for t, _ in suggestions]
        assert "python" in tags or "code" in tags

    def test_get_tag_suggestions_excludes_existing(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_learning import TagLearningEngine

        engine = TagLearningEngine(storage_path=tmp_path / "tags.json")
        f = tmp_path / "excl.py"
        f.touch()
        for _ in range(3):
            engine.record_tag_application(f, ["already_there", "suggest_me"])

        suggestions = engine.get_tag_suggestions_for_context(
            file_type=".py", existing_tags=["already_there"]
        )
        tags = [t for t, _ in suggestions]
        assert "already_there" not in tags

    def test_tag_patterns_frequency(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_learning import TagLearningEngine

        engine = TagLearningEngine(storage_path=tmp_path / "tags.json")
        f = tmp_path / "patt.txt"
        f.touch()
        for _ in range(5):
            engine.record_tag_application(f, ["patt_tag"])

        patterns = engine.get_tag_patterns()
        freq_patterns = [p for p in patterns if p.pattern_type == "frequency"]
        assert any(p.tags == ["patt_tag"] for p in freq_patterns)

    def test_tag_patterns_filtered_by_file_type(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_learning import TagLearningEngine

        engine = TagLearningEngine(storage_path=tmp_path / "tags.json")
        pdf = tmp_path / "filtered.pdf"
        txt = tmp_path / "filtered.txt"
        pdf.touch()
        txt.touch()
        engine.record_tag_application(pdf, ["pdf_only"])
        engine.record_tag_application(txt, ["txt_only"])

        patterns = engine.get_tag_patterns(file_type=".pdf")
        tags_seen = [p.tags[0] for p in patterns if p.pattern_type == "frequency"]
        assert "pdf_only" in tags_seen
        assert "txt_only" not in tags_seen

    def test_persistence_across_instances(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_learning import TagLearningEngine

        storage = tmp_path / "persist_tags.json"
        engine = TagLearningEngine(storage_path=storage)
        f = tmp_path / "persist.txt"
        f.touch()
        engine.record_tag_application(f, ["persist_tag"])

        engine2 = TagLearningEngine(storage_path=storage)
        assert "persist_tag" in engine2.tag_usage

    def test_tag_usage_serialization(self) -> None:
        from services.auto_tagging.tag_learning import TagUsage

        now = datetime.now(UTC)
        usage = TagUsage(tag="ser", count=5, first_used=now, last_used=now, file_types={".txt"})
        d = usage.to_dict()
        restored = TagUsage.from_dict(d)
        assert restored.tag == "ser"
        assert restored.count == 5
        assert ".txt" in restored.file_types

    def test_tag_pattern_serialization(self) -> None:
        from services.auto_tagging.tag_learning import TagPattern

        now = datetime.now(UTC)
        pattern = TagPattern(
            pattern_type="co-occurrence",
            tags=["a", "b"],
            frequency=3.0,
            confidence=75.0,
            last_seen=now,
        )
        d = pattern.to_dict()
        restored = TagPattern.from_dict(d)
        assert restored.pattern_type == "co-occurrence"
        assert restored.tags == ["a", "b"]


# ---------------------------------------------------------------------------
# TagRecommender
# ---------------------------------------------------------------------------


class TestTagRecommender:
    def test_recommend_tags_for_nonexistent_file(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_recommender import TagRecommender

        recommender = TagRecommender(
            learning_engine=MagicMock(
                get_tag_suggestions_for_context=MagicMock(return_value=[]),
                predict_tags=MagicMock(return_value=[]),
                get_related_tags=MagicMock(return_value=[]),
                file_type_tags={},
                directory_tags={},
                tag_usage={},
                tag_cooccurrence={},
            )
        )
        result = recommender.recommend_tags(tmp_path / "ghost.txt")
        assert result.suggestions == []

    def test_recommend_tags_basic(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_learning import TagLearningEngine
        from services.auto_tagging.tag_recommender import TagRecommender

        storage = tmp_path / "rec_tags.json"
        learning = TagLearningEngine(storage_path=storage)
        target = tmp_path / "target.txt"
        target.write_text("hello world python programming")

        for _ in range(5):
            training = tmp_path / "train.txt"
            training.write_text("sample")
            training.touch()
            learning.record_tag_application(target, ["text", "document"])

        recommender = TagRecommender(learning_engine=learning, min_confidence=0.0)
        rec = recommender.recommend_tags(target)
        assert rec.file_path == target
        suggested_tags = [s.tag for s in rec.suggestions]
        assert "text" in suggested_tags

    def test_recommend_tags_filters_existing(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_learning import TagLearningEngine
        from services.auto_tagging.tag_recommender import TagRecommender

        storage = tmp_path / "filter_tags.json"
        learning = TagLearningEngine(storage_path=storage)
        f = tmp_path / "doc.txt"
        f.write_text("content")
        for _ in range(5):
            learning.record_tag_application(f, ["existing", "new_tag"])

        recommender = TagRecommender(learning_engine=learning, min_confidence=0.0)
        rec = recommender.recommend_tags(f, existing_tags=["existing"])
        suggested_tags = [s.tag for s in rec.suggestions]
        assert "existing" not in suggested_tags

    def test_batch_recommend(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_recommender import TagRecommender

        f1 = tmp_path / "b1.txt"
        f2 = tmp_path / "b2.txt"
        f1.write_text("file one")
        f2.write_text("file two")

        recommender = TagRecommender(min_confidence=0.0)
        results = recommender.batch_recommend([f1, f2])
        assert f1 in results
        assert f2 in results

    def test_calculate_confidence_no_match(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_recommender import TagRecommender

        f = tmp_path / "conf.txt"
        f.write_text("nothing")
        recommender = TagRecommender()
        score = recommender.calculate_confidence("unknown_tag", f)
        assert score == 0.0

    def test_explain_tag(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_learning import TagLearningEngine
        from services.auto_tagging.tag_recommender import TagRecommender

        storage = tmp_path / "explain_tags.json"
        learning = TagLearningEngine(storage_path=storage)
        f = tmp_path / "explain.txt"
        f.write_text("content")
        for _ in range(3):
            learning.record_tag_application(f, ["explained"])

        recommender = TagRecommender(learning_engine=learning)
        explanation = recommender.explain_tag("explained", f)
        assert isinstance(explanation, str)
        assert len(explanation) > 0

    def test_tag_recommendation_high_confidence(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_recommender import (
            TagRecommendation,
            TagSuggestion,
        )

        f = tmp_path / "rec.txt"
        f.touch()
        suggestions = [
            TagSuggestion(tag="hi", confidence=80, source="content", reasoning="found"),
            TagSuggestion(tag="lo", confidence=30, source="content", reasoning="maybe"),
        ]
        rec = TagRecommendation(file_path=f, suggestions=suggestions)
        assert "hi" in rec.get_high_confidence_tags()
        assert "lo" not in rec.get_high_confidence_tags()

    def test_tag_recommendation_medium_confidence(self, tmp_path: Path) -> None:
        from services.auto_tagging.tag_recommender import (
            TagRecommendation,
            TagSuggestion,
        )

        f = tmp_path / "med.txt"
        f.touch()
        suggestions = [
            TagSuggestion(tag="med", confidence=55, source="behavior", reasoning="patterns"),
            TagSuggestion(tag="hi", confidence=80, source="hybrid", reasoning="both"),
        ]
        rec = TagRecommendation(file_path=f, suggestions=suggestions)
        assert "med" in rec.get_medium_confidence_tags()
        assert "hi" not in rec.get_medium_confidence_tags()

    def test_tag_suggestion_serialization(self) -> None:
        from services.auto_tagging.tag_recommender import TagSuggestion

        s = TagSuggestion(
            tag="test",
            confidence=75.0,
            source="hybrid",
            reasoning="Combined signals",
            metadata={"key": "value"},
        )
        d = s.to_dict()
        restored = TagSuggestion.from_dict(d)
        assert restored.tag == "test"
        assert restored.confidence == 75.0
        assert restored.source == "hybrid"
        assert restored.metadata["key"] == "value"


# ---------------------------------------------------------------------------
# AutoTaggingService
# ---------------------------------------------------------------------------


class TestAutoTaggingService:
    def test_suggest_tags_for_missing_file(self, tmp_path: Path) -> None:
        from services.auto_tagging import AutoTaggingService

        svc = AutoTaggingService(storage_path=tmp_path / "auto_tags.json")
        rec = svc.suggest_tags(tmp_path / "ghost.txt")
        assert rec.suggestions == []

    def test_record_tag_usage(self, tmp_path: Path) -> None:
        from services.auto_tagging import AutoTaggingService

        svc = AutoTaggingService(storage_path=tmp_path / "auto_tags.json")
        f = tmp_path / "tagged.txt"
        f.write_text("tagged content")
        svc.record_tag_usage(f, ["tag1", "tag2"])
        popular = svc.get_popular_tags()
        tags = [t for t, _ in popular]
        assert "tag1" in tags
        assert "tag2" in tags

    def test_provide_feedback(self, tmp_path: Path) -> None:
        from services.auto_tagging import AutoTaggingService

        svc = AutoTaggingService(storage_path=tmp_path / "auto_tags.json")
        f = tmp_path / "fb.txt"
        f.touch()
        svc.record_tag_usage(f, ["fb_tag"])

        feedback = [
            {
                "file_path": str(f),
                "accepted_tags": ["fb_tag"],
                "rejected_tags": [],
            }
        ]
        svc.provide_feedback(feedback)
        popular_tags = [tag for tag, _ in svc.get_popular_tags()]
        assert "fb_tag" in popular_tags

    def test_get_popular_tags_empty(self, tmp_path: Path) -> None:
        from services.auto_tagging import AutoTaggingService

        svc = AutoTaggingService(storage_path=tmp_path / "empty_tags.json")
        assert svc.get_popular_tags() == []

    def test_get_recent_tags(self, tmp_path: Path) -> None:
        from services.auto_tagging import AutoTaggingService

        svc = AutoTaggingService(storage_path=tmp_path / "recent_tags.json")
        f = tmp_path / "recent.txt"
        f.touch()
        svc.record_tag_usage(f, ["recent_tag"])
        recent = svc.get_recent_tags(days=7)
        assert "recent_tag" in recent

    def test_suggest_tags_with_existing_tags(self, tmp_path: Path) -> None:
        from services.auto_tagging import AutoTaggingService

        svc = AutoTaggingService(storage_path=tmp_path / "exist_tags.json")
        f = tmp_path / "exist.txt"
        f.write_text("some text content")
        rec = svc.suggest_tags(f, existing_tags=["already"], top_n=5)
        suggested = [s.tag for s in rec.suggestions]
        assert "already" not in suggested


# ---------------------------------------------------------------------------
# PreviewEngine (copilot rules)
# ---------------------------------------------------------------------------


class TestPreviewEngine:
    def _make_rule_set(self, rules=None):
        from services.copilot.rules.models import RuleSet

        return RuleSet(name="test_set", rules=rules or [])

    def _make_rule(
        self, name, conditions=None, action_type="move", destination="", enabled=True, priority=0
    ):
        from services.copilot.rules.models import (
            ActionType,
            Rule,
            RuleAction,
        )

        action = RuleAction(action_type=ActionType(action_type), destination=destination)
        return Rule(
            name=name,
            conditions=conditions or [],
            action=action,
            enabled=enabled,
            priority=priority,
        )

    def _make_condition(self, ctype, value, negate=False):
        from services.copilot.rules.models import ConditionType, RuleCondition

        return RuleCondition(condition_type=ConditionType(ctype), value=value, negate=negate)

    def test_preview_empty_directory(self, tmp_path: Path) -> None:
        from services.copilot.rules.preview import PreviewEngine

        rule_set = self._make_rule_set()
        engine = PreviewEngine()
        result = engine.preview(rule_set, tmp_path)
        assert result.total_files == 0

    def test_preview_no_enabled_rules(self, tmp_path: Path) -> None:
        from services.copilot.rules.preview import PreviewEngine

        (tmp_path / "file.txt").write_text("hello")
        rule = self._make_rule("disabled", enabled=False)
        rule_set = self._make_rule_set([rule])
        engine = PreviewEngine()
        result = engine.preview(rule_set, tmp_path)
        assert result.match_count == 0

    def test_preview_extension_match(self, tmp_path: Path) -> None:
        from services.copilot.rules.preview import PreviewEngine

        (tmp_path / "doc.pdf").write_bytes(b"pdf")
        (tmp_path / "image.png").write_bytes(b"png")
        cond = self._make_condition("extension", ".pdf")
        rule = self._make_rule("pdf_rule", conditions=[cond], destination="/archive/pdfs")
        rule_set = self._make_rule_set([rule])
        engine = PreviewEngine()
        result = engine.preview(rule_set, tmp_path)
        assert result.match_count == 1
        assert result.matches[0].rule_name == "pdf_rule"
        assert len(result.unmatched) == 1

    def test_preview_name_pattern_match(self, tmp_path: Path) -> None:
        from services.copilot.rules.preview import PreviewEngine

        (tmp_path / "report_2024.txt").write_text("report")
        (tmp_path / "notes.txt").write_text("notes")
        cond = self._make_condition("name_pattern", "report_*.txt")
        rule = self._make_rule("report_rule", conditions=[cond])
        rule_set = self._make_rule_set([rule])
        engine = PreviewEngine()
        result = engine.preview(rule_set, tmp_path)
        assert result.match_count == 1
        assert "report_2024.txt" in result.matches[0].file_path

    def test_preview_size_greater_match(self, tmp_path: Path) -> None:
        from services.copilot.rules.preview import PreviewEngine

        big = tmp_path / "big.bin"
        big.write_bytes(b"x" * 1000)
        small = tmp_path / "small.bin"
        small.write_bytes(b"x" * 10)
        cond = self._make_condition("size_greater", "500")
        rule = self._make_rule("big_rule", conditions=[cond])
        rule_set = self._make_rule_set([rule])
        engine = PreviewEngine()
        result = engine.preview(rule_set, tmp_path)
        assert result.match_count == 1
        assert "big.bin" in result.matches[0].file_path

    def test_preview_size_less_match(self, tmp_path: Path) -> None:
        from services.copilot.rules.preview import PreviewEngine

        small = tmp_path / "tiny.txt"
        small.write_bytes(b"x" * 5)
        big = tmp_path / "large.txt"
        big.write_bytes(b"x" * 5000)
        cond = self._make_condition("size_less", "100")
        rule = self._make_rule("small_rule", conditions=[cond])
        rule_set = self._make_rule_set([rule])
        engine = PreviewEngine()
        result = engine.preview(rule_set, tmp_path)
        assert result.match_count == 1

    def test_preview_content_contains_match(self, tmp_path: Path) -> None:
        from services.copilot.rules.preview import PreviewEngine

        (tmp_path / "invoice.txt").write_text("INVOICE amount due")
        (tmp_path / "notes.txt").write_text("random notes here")
        cond = self._make_condition("content_contains", "invoice")
        rule = self._make_rule("invoice_rule", conditions=[cond])
        rule_set = self._make_rule_set([rule])
        engine = PreviewEngine()
        result = engine.preview(rule_set, tmp_path)
        assert result.match_count == 1

    def test_preview_modified_before_match(self, tmp_path: Path) -> None:
        from services.copilot.rules.preview import PreviewEngine

        f = tmp_path / "old.txt"
        f.write_text("old file")
        future = (datetime.now(UTC) + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cond = self._make_condition("modified_before", future)
        rule = self._make_rule("old_rule", conditions=[cond])
        rule_set = self._make_rule_set([rule])
        engine = PreviewEngine()
        result = engine.preview(rule_set, tmp_path)
        assert result.match_count == 1

    def test_preview_modified_after_match(self, tmp_path: Path) -> None:
        from services.copilot.rules.preview import PreviewEngine

        f = tmp_path / "new.txt"
        f.write_text("new file")
        past = (datetime.now(UTC) - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cond = self._make_condition("modified_after", past)
        rule = self._make_rule("new_rule", conditions=[cond])
        rule_set = self._make_rule_set([rule])
        engine = PreviewEngine()
        result = engine.preview(rule_set, tmp_path)
        assert result.match_count == 1

    def test_preview_path_matches_regex(self, tmp_path: Path) -> None:
        from services.copilot.rules.preview import PreviewEngine

        (tmp_path / "project_alpha.txt").write_text("alpha")
        (tmp_path / "readme.md").write_text("readme")
        cond = self._make_condition("path_matches", r"project_\w+\.txt")
        rule = self._make_rule("path_rule", conditions=[cond])
        rule_set = self._make_rule_set([rule])
        engine = PreviewEngine()
        result = engine.preview(rule_set, tmp_path)
        assert result.match_count == 1

    def test_preview_negated_condition(self, tmp_path: Path) -> None:
        from services.copilot.rules.preview import PreviewEngine

        (tmp_path / "doc.pdf").write_bytes(b"pdf")
        (tmp_path / "doc.txt").write_text("text")
        cond = self._make_condition("extension", ".pdf", negate=True)
        rule = self._make_rule("not_pdf_rule", conditions=[cond])
        rule_set = self._make_rule_set([rule])
        engine = PreviewEngine()
        result = engine.preview(rule_set, tmp_path)
        assert result.match_count == 1
        assert "doc.txt" in result.matches[0].file_path

    def test_preview_first_rule_wins(self, tmp_path: Path) -> None:
        from services.copilot.rules.preview import PreviewEngine

        (tmp_path / "file.txt").write_text("content")
        cond1 = self._make_condition("extension", ".txt")
        cond2 = self._make_condition("extension", ".txt")
        rule1 = self._make_rule("first_rule", conditions=[cond1], priority=10, destination="dest1")
        rule2 = self._make_rule("second_rule", conditions=[cond2], priority=5, destination="dest2")
        rule_set = self._make_rule_set([rule1, rule2])
        engine = PreviewEngine()
        result = engine.preview(rule_set, tmp_path)
        assert result.match_count == 1
        assert result.matches[0].rule_name == "first_rule"

    def test_preview_target_not_directory(self, tmp_path: Path) -> None:
        from services.copilot.rules.preview import PreviewEngine

        f = tmp_path / "not_a_dir.txt"
        f.write_text("file")
        rule_set = self._make_rule_set()
        engine = PreviewEngine()
        result = engine.preview(rule_set, f)
        assert len(result.errors) == 1

    def test_preview_summary_string(self) -> None:
        from services.copilot.rules.preview import PreviewResult

        result = PreviewResult(total_files=5)
        result.matches = []
        result.unmatched = ["a", "b", "c"]
        result.errors = []
        assert "3 unmatched" in result.summary
        assert "5 total" in result.summary

    def test_preview_destination_template(self, tmp_path: Path) -> None:
        from services.copilot.rules.preview import PreviewEngine

        f = tmp_path / "report.pdf"
        f.write_bytes(b"data")
        cond = self._make_condition("extension", ".pdf")
        rule = self._make_rule(
            "template_rule", conditions=[cond], destination="/out/{stem}_archived.{ext}"
        )
        rule_set = self._make_rule_set([rule])
        engine = PreviewEngine()
        result = engine.preview(rule_set, tmp_path)
        assert result.match_count == 1
        assert "report_archived" in result.matches[0].destination

    def test_preview_multiple_conditions_and_logic(self, tmp_path: Path) -> None:
        from services.copilot.rules.preview import PreviewEngine

        big_pdf = tmp_path / "big.pdf"
        big_pdf.write_bytes(b"x" * 2000)
        small_pdf = tmp_path / "small.pdf"
        small_pdf.write_bytes(b"x" * 10)
        cond_ext = self._make_condition("extension", ".pdf")
        cond_size = self._make_condition("size_greater", "500")
        rule = self._make_rule("big_pdf_rule", conditions=[cond_ext, cond_size])
        rule_set = self._make_rule_set([rule])
        engine = PreviewEngine()
        result = engine.preview(rule_set, tmp_path)
        assert result.match_count == 1
        assert "big.pdf" in result.matches[0].file_path

    def test_preview_non_recursive(self, tmp_path: Path) -> None:
        from services.copilot.rules.preview import PreviewEngine

        (tmp_path / "top.txt").write_text("top")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "nested.txt").write_text("nested")

        cond = self._make_condition("extension", ".txt")
        rule = self._make_rule("txt_rule", conditions=[cond])
        rule_set = self._make_rule_set([rule])
        engine = PreviewEngine()
        result_recursive = engine.preview(rule_set, tmp_path, recursive=True)
        result_flat = engine.preview(rule_set, tmp_path, recursive=False)
        assert result_recursive.match_count > result_flat.match_count

    def test_preview_max_files_limit(self, tmp_path: Path) -> None:
        from services.copilot.rules.preview import PreviewEngine

        for i in range(20):
            (tmp_path / f"file_{i}.txt").write_text(f"content {i}")
        cond = self._make_condition("extension", ".txt")
        rule = self._make_rule("limit_rule", conditions=[cond])
        rule_set = self._make_rule_set([rule])
        engine = PreviewEngine()
        result = engine.preview(rule_set, tmp_path, max_files=5)
        assert result.total_files <= 5


# ---------------------------------------------------------------------------
# CommandExecutor (additional branches)
# ---------------------------------------------------------------------------


class TestCommandExecutorAdditional:
    def _make_intent(self, intent_type, params=None):
        from services.copilot.executor import Intent, IntentType

        return Intent(intent_type=IntentType(intent_type), parameters=params or {})

    def test_execute_find_empty_query(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        executor = CommandExecutor(working_directory=str(tmp_path))
        intent = self._make_intent("find", {"query": ""})
        result = executor.execute(intent)
        assert result.success is False
        assert "search for" in result.message.lower()

    def test_execute_find_with_matches(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        (tmp_path / "report.txt").write_text("content")
        executor = CommandExecutor(working_directory=str(tmp_path))
        intent = self._make_intent("find", {"query": "report", "paths": [str(tmp_path)]})
        result = executor.execute(intent)
        assert result.success is True
        assert len(result.affected_files) >= 1

    def test_execute_find_no_matches(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        executor = CommandExecutor(working_directory=str(tmp_path))
        intent = self._make_intent(
            "find", {"query": "nonexistentxyzfile", "paths": [str(tmp_path)]}
        )
        result = executor.execute(intent)
        assert result.success is True
        assert "No files" in result.message

    def test_execute_move_missing_params(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        executor = CommandExecutor(working_directory=str(tmp_path))
        intent = self._make_intent("move", {"source": "a.txt"})
        result = executor.execute(intent)
        assert result.success is False
        assert "destination" in result.message.lower() or "specify" in result.message.lower()

    def test_execute_move_source_not_found(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        executor = CommandExecutor(working_directory=str(tmp_path))
        intent = self._make_intent(
            "move",
            {"source": str(tmp_path / "ghost.txt"), "destination": str(tmp_path / "dest.txt")},
        )
        result = executor.execute(intent)
        assert result.success is False
        assert "not found" in result.message.lower()

    def test_execute_move_success(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        src = tmp_path / "move_me.txt"
        src.write_text("moving")
        dst = tmp_path / "moved.txt"
        executor = CommandExecutor(working_directory=str(tmp_path))
        intent = self._make_intent("move", {"source": str(src), "destination": str(dst)})
        result = executor.execute(intent)
        assert result.success is True
        assert dst.exists()

    def test_execute_rename_missing_params(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        executor = CommandExecutor(working_directory=str(tmp_path))
        intent = self._make_intent("rename", {"target": "file.txt"})
        result = executor.execute(intent)
        assert result.success is False

    def test_execute_rename_file_not_found(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        executor = CommandExecutor(working_directory=str(tmp_path))
        intent = self._make_intent(
            "rename",
            {"target": str(tmp_path / "ghost.txt"), "new_name": "new.txt"},
        )
        result = executor.execute(intent)
        assert result.success is False

    def test_execute_rename_success(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        f = tmp_path / "original.txt"
        f.write_text("data")
        executor = CommandExecutor(working_directory=str(tmp_path))
        intent = self._make_intent("rename", {"target": str(f), "new_name": "renamed.txt"})
        result = executor.execute(intent)
        assert result.success is True
        assert (tmp_path / "renamed.txt").exists()

    def test_execute_suggest_missing_paths(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        executor = CommandExecutor(working_directory=str(tmp_path))
        intent = self._make_intent("suggest", {"paths": []})
        result = executor.execute(intent)
        assert result.success is False

    def test_execute_suggest_with_existing_path(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        f = tmp_path / "target.txt"
        f.write_text("data")
        executor = CommandExecutor(working_directory=str(tmp_path))
        intent = self._make_intent("suggest", {"paths": [str(f)]})
        result = executor.execute(intent)
        assert result.success is True

    def test_execute_suggest_path_not_found(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        executor = CommandExecutor(working_directory=str(tmp_path))
        intent = self._make_intent("suggest", {"paths": [str(tmp_path / "ghost.txt")]})
        result = executor.execute(intent)
        assert result.success is False

    def test_execute_undo_no_history(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        executor = CommandExecutor(working_directory=str(tmp_path))
        intent = self._make_intent("undo")
        result = executor.execute(intent)
        assert result.success is False

    def test_execute_redo_no_history(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        executor = CommandExecutor(working_directory=str(tmp_path))
        intent = self._make_intent("redo")
        result = executor.execute(intent)
        assert result.success is False

    def test_execute_preview_non_directory(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        f = tmp_path / "not_dir.txt"
        f.write_text("data")
        executor = CommandExecutor(working_directory=str(tmp_path))
        intent = self._make_intent("preview", {"source": str(f)})
        result = executor.execute(intent)
        assert result.success is False

    def test_execute_handler_exception_returns_failure(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor, Intent, IntentType

        executor = CommandExecutor(working_directory=str(tmp_path))
        intent = Intent(intent_type=IntentType.MOVE, parameters={})
        with patch.object(executor, "_handle_move", side_effect=RuntimeError("boom")):
            result = executor.execute(intent)
        assert result.success is False
        assert "boom" in result.message

    def test_execute_find_with_retriever_semantic(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        mock_retriever = MagicMock()
        mock_retriever.is_initialized = True
        target_file = tmp_path / "semantic_match.txt"
        target_file.write_text("some content")
        mock_retriever.retrieve.return_value = [(target_file, 0.9)]

        executor = CommandExecutor(working_directory=str(tmp_path), retriever=mock_retriever)
        intent = self._make_intent("find", {"query": "content", "paths": [str(tmp_path)]})
        result = executor.execute(intent)
        assert result.success is True
        mock_retriever.retrieve.assert_called_once()
        assert mock_retriever.retrieve.call_args.args[0] == "content"

    def test_execute_organize_directory_not_found(self, tmp_path: Path) -> None:
        from services.copilot.executor import CommandExecutor

        executor = CommandExecutor(working_directory=str(tmp_path))
        intent = self._make_intent("organize", {"source": str(tmp_path / "missing_dir")})
        result = executor.execute(intent)
        assert result.success is False


# ---------------------------------------------------------------------------
# SuggestionFeedback
# ---------------------------------------------------------------------------


class TestSuggestionFeedback:
    def _make_suggestion(self, suggestion_id="s1", stype="move", confidence=75.0):
        from models.suggestion_types import Suggestion, SuggestionType

        return Suggestion(
            suggestion_id=suggestion_id,
            suggestion_type=SuggestionType(stype),
            file_path=Path("/fake/file.txt"),
            target_path=Path("/fake/dest/"),
            confidence=confidence,
        )

    def test_record_action_accepted(self, tmp_path: Path) -> None:
        from services.suggestion_feedback import SuggestionFeedback

        fb = SuggestionFeedback(feedback_file=tmp_path / "feedback.json")
        suggestion = self._make_suggestion()
        fb.record_action(suggestion, "accepted")
        assert len(fb.feedback_entries) == 1
        assert fb.feedback_entries[0].action == "accepted"

    def test_record_action_rejected(self, tmp_path: Path) -> None:
        from services.suggestion_feedback import SuggestionFeedback

        fb = SuggestionFeedback(feedback_file=tmp_path / "feedback.json")
        suggestion = self._make_suggestion()
        fb.record_action(suggestion, "rejected")
        assert fb.feedback_entries[0].action == "rejected"

    def test_record_action_with_metadata(self, tmp_path: Path) -> None:
        from services.suggestion_feedback import SuggestionFeedback

        fb = SuggestionFeedback(feedback_file=tmp_path / "feedback.json")
        suggestion = self._make_suggestion()
        fb.record_action(suggestion, "modified", metadata={"reason": "test"})
        assert fb.feedback_entries[0].metadata["reason"] == "test"

    def test_persistence_across_instances(self, tmp_path: Path) -> None:
        from services.suggestion_feedback import SuggestionFeedback

        feedback_file = tmp_path / "persist_feedback.json"
        fb = SuggestionFeedback(feedback_file=feedback_file)
        suggestion = self._make_suggestion()
        fb.record_action(suggestion, "accepted")

        fb2 = SuggestionFeedback(feedback_file=feedback_file)
        assert len(fb2.feedback_entries) == 1

    def test_get_acceptance_rate_all_accepted(self, tmp_path: Path) -> None:
        from services.suggestion_feedback import SuggestionFeedback

        fb = SuggestionFeedback(feedback_file=tmp_path / "feedback.json")
        for i in range(4):
            fb.record_action(self._make_suggestion(f"s{i}"), "accepted")
        rate = fb.get_acceptance_rate()
        assert rate == 100.0

    def test_get_acceptance_rate_mixed(self, tmp_path: Path) -> None:
        from services.suggestion_feedback import SuggestionFeedback

        fb = SuggestionFeedback(feedback_file=tmp_path / "feedback.json")
        fb.record_action(self._make_suggestion("s1"), "accepted")
        fb.record_action(self._make_suggestion("s2"), "rejected")
        rate = fb.get_acceptance_rate()
        assert rate == 50.0

    def test_get_acceptance_rate_empty(self, tmp_path: Path) -> None:
        from services.suggestion_feedback import SuggestionFeedback

        fb = SuggestionFeedback(feedback_file=tmp_path / "feedback.json")
        assert fb.get_acceptance_rate() == 0.0

    def test_get_rejection_rate(self, tmp_path: Path) -> None:
        from services.suggestion_feedback import SuggestionFeedback

        fb = SuggestionFeedback(feedback_file=tmp_path / "feedback.json")
        fb.record_action(self._make_suggestion("s1"), "rejected")
        fb.record_action(self._make_suggestion("s2"), "rejected")
        fb.record_action(self._make_suggestion("s3"), "accepted")
        rate = fb.get_rejection_rate()
        assert rate == pytest.approx(66.67, rel=0.01)

    def test_get_acceptance_rate_by_type(self, tmp_path: Path) -> None:
        from services.suggestion_feedback import SuggestionFeedback

        fb = SuggestionFeedback(feedback_file=tmp_path / "feedback.json")
        fb.record_action(self._make_suggestion("s1", stype="move"), "accepted")
        fb.record_action(self._make_suggestion("s2", stype="rename"), "rejected")
        move_rate = fb.get_acceptance_rate(suggestion_type="move")
        rename_rate = fb.get_acceptance_rate(suggestion_type="rename")
        assert move_rate == 100.0
        assert rename_rate == 0.0

    def test_get_learning_stats_full(self, tmp_path: Path) -> None:
        from services.suggestion_feedback import SuggestionFeedback

        fb = SuggestionFeedback(feedback_file=tmp_path / "feedback.json")
        fb.record_action(self._make_suggestion("s1", confidence=80.0), "accepted")
        fb.record_action(self._make_suggestion("s2", confidence=40.0), "rejected")
        fb.record_action(self._make_suggestion("s3", confidence=50.0), "ignored")
        fb.record_action(self._make_suggestion("s4", confidence=60.0), "modified")

        stats = fb.get_learning_stats()
        assert stats.total_suggestions == 4
        assert stats.accepted == 1
        assert stats.rejected == 1
        assert stats.ignored == 1
        assert stats.modified == 1
        assert stats.acceptance_rate == 25.0
        assert stats.avg_accepted_confidence == 80.0
        assert stats.avg_rejected_confidence == 40.0

    def test_get_learning_stats_empty(self, tmp_path: Path) -> None:
        from services.suggestion_feedback import SuggestionFeedback

        fb = SuggestionFeedback(feedback_file=tmp_path / "feedback.json")
        stats = fb.get_learning_stats()
        assert stats.total_suggestions == 0
        assert stats.acceptance_rate == 0.0

    def test_learning_stats_by_type(self, tmp_path: Path) -> None:
        from services.suggestion_feedback import SuggestionFeedback

        fb = SuggestionFeedback(feedback_file=tmp_path / "feedback.json")
        fb.record_action(self._make_suggestion("s1", stype="move"), "accepted")
        fb.record_action(self._make_suggestion("s2", stype="move"), "rejected")
        fb.record_action(self._make_suggestion("s3", stype="tag"), "accepted")

        stats = fb.get_learning_stats()
        assert "move" in stats.by_type
        assert stats.by_type["move"]["total"] == 2
        assert stats.by_type["move"]["accepted"] == 1

    def test_get_confidence_adjustment_no_data(self, tmp_path: Path) -> None:
        from models.suggestion_types import SuggestionType
        from services.suggestion_feedback import SuggestionFeedback

        fb = SuggestionFeedback(feedback_file=tmp_path / "feedback.json")
        adj = fb.get_confidence_adjustment(SuggestionType.MOVE, ".pdf")
        assert adj == 0.0

    def test_get_confidence_adjustment_after_accepted(self, tmp_path: Path) -> None:
        from models.suggestion_types import SuggestionType
        from services.suggestion_feedback import SuggestionFeedback

        fb = SuggestionFeedback(feedback_file=tmp_path / "feedback.json")
        for _ in range(5):
            fb.record_action(self._make_suggestion("s1", stype="move", confidence=60.0), "accepted")
        adj = fb.get_confidence_adjustment(SuggestionType.MOVE, ".txt")
        assert adj > 0.0

    def test_get_confidence_adjustment_after_rejected(self, tmp_path: Path) -> None:
        from models.suggestion_types import SuggestionType
        from services.suggestion_feedback import SuggestionFeedback

        fb = SuggestionFeedback(feedback_file=tmp_path / "feedback.json")
        for _ in range(5):
            fb.record_action(self._make_suggestion("s1", stype="move", confidence=80.0), "rejected")
        adj = fb.get_confidence_adjustment(SuggestionType.MOVE, ".txt")
        assert adj < 0.0

    def test_update_patterns(self, tmp_path: Path) -> None:
        from models.suggestion_types import SuggestionType
        from services.suggestion_feedback import FeedbackEntry, SuggestionFeedback

        fb = SuggestionFeedback(feedback_file=tmp_path / "feedback.json")
        entries = [
            FeedbackEntry(
                suggestion_id="u1",
                suggestion_type=SuggestionType.RENAME,
                action="accepted",
                file_path=str(tmp_path / "f.txt"),
                target_path=None,
                confidence=70.0,
            )
        ]
        fb.update_patterns(entries)
        key = "rename:.txt"
        assert key in fb.pattern_adjustments

    def test_get_user_history(self, tmp_path: Path) -> None:
        from models.suggestion_types import Suggestion, SuggestionType
        from services.suggestion_feedback import SuggestionFeedback

        fb = SuggestionFeedback(feedback_file=tmp_path / "feedback.json")
        s = Suggestion(
            suggestion_id="h1",
            suggestion_type=SuggestionType.MOVE,
            file_path=Path("/fake/doc.pdf"),
            target_path=Path("/fake/archive/doc.pdf"),
            confidence=85.0,
        )
        fb.record_action(s, "accepted")
        history = fb.get_user_history()
        assert "move_history" in history
        assert "preferred_locations" in history

    def test_clear_old_feedback(self, tmp_path: Path) -> None:
        from models.suggestion_types import SuggestionType
        from services.suggestion_feedback import FeedbackEntry, SuggestionFeedback

        fb = SuggestionFeedback(feedback_file=tmp_path / "feedback.json")
        old_entry = FeedbackEntry(
            suggestion_id="old1",
            suggestion_type=SuggestionType.MOVE,
            action="accepted",
            file_path="/fake/old.txt",
            target_path=None,
            confidence=70.0,
            timestamp=datetime.now(UTC) - timedelta(days=100),
        )
        fb.feedback_entries.append(old_entry)
        new_suggestion = self._make_suggestion("new1")
        fb.record_action(new_suggestion, "accepted")

        removed = fb.clear_old_feedback(days=30)
        assert removed >= 1
        assert all(
            e.timestamp.timestamp() > (datetime.now(UTC) - timedelta(days=30)).timestamp()
            for e in fb.feedback_entries
        )

    def test_export_feedback(self, tmp_path: Path) -> None:
        from services.suggestion_feedback import SuggestionFeedback

        fb = SuggestionFeedback(feedback_file=tmp_path / "feedback.json")
        fb.record_action(self._make_suggestion(), "accepted")
        out = tmp_path / "export.json"
        fb.export_feedback(out)
        assert out.exists()
        data = json.loads(out.read_text())
        assert "entries" in data
        assert "stats" in data
        assert "exported_at" in data

    def test_feedback_entry_serialization(self) -> None:
        from models.suggestion_types import SuggestionType
        from services.suggestion_feedback import FeedbackEntry

        now = datetime.now(UTC)
        entry = FeedbackEntry(
            suggestion_id="e1",
            suggestion_type=SuggestionType.TAG,
            action="accepted",
            file_path="/fake/f.txt",
            target_path="/fake/dest/",
            confidence=80.0,
            timestamp=now,
            metadata={"key": "val"},
        )
        d = entry.to_dict()
        assert d["action"] == "accepted"
        assert d["suggestion_type"] == "tag"

        restored = FeedbackEntry.from_dict(d)
        assert restored.suggestion_id == "e1"
        assert restored.suggestion_type == SuggestionType.TAG

    def test_load_corrupted_feedback_file(self, tmp_path: Path) -> None:
        from services.suggestion_feedback import SuggestionFeedback

        feedback_file = tmp_path / "corrupt.json"
        feedback_file.write_text("{invalid json}")
        fb = SuggestionFeedback(feedback_file=feedback_file)
        assert fb.feedback_entries == []
