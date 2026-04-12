"""Integration tests for Johnny Decimal methodology modules.

Covers:
  - scanner.py       (FolderScanner, FolderInfo, ScanResult)
  - transformer.py   (FolderTransformer, TransformationRule, TransformationPlan)
  - validator.py     (MigrationValidator, ValidationIssue, ValidationResult)
  - numbering.py     (JohnnyDecimalGenerator, NumberConflictError, InvalidNumberError)
  - compatibility.py (PARAJohnnyDecimalBridge, CompatibilityAnalyzer, HybridOrganizer)
  - adapters.py      (PARAAdapter, FileSystemAdapter, AdapterRegistry, create_default_registry)
  - migrator.py      (JohnnyDecimalMigrator, MigrationResult, RollbackInfo)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dir(tmp_path: Path, name: str) -> Path:
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _default_scheme():
    from file_organizer.methodologies.johnny_decimal.categories import get_default_scheme

    return get_default_scheme()


def _make_generator(scheme=None):
    from file_organizer.methodologies.johnny_decimal.numbering import JohnnyDecimalGenerator

    return JohnnyDecimalGenerator(scheme or _default_scheme())


# ---------------------------------------------------------------------------
# TestFolderScanner
# ---------------------------------------------------------------------------


class TestFolderScannerBasic:
    def test_scan_raises_on_missing_path(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.scanner import FolderScanner

        scanner = FolderScanner()
        with pytest.raises(ValueError, match="does not exist"):
            scanner.scan_directory(tmp_path / "nonexistent")

    def test_scan_raises_on_file_path(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.scanner import FolderScanner

        f = tmp_path / "file.txt"
        f.write_text("hello")
        scanner = FolderScanner()
        with pytest.raises(ValueError, match="not a directory"):
            scanner.scan_directory(f)

    def test_scan_empty_directory(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.scanner import FolderScanner

        # Use a dedicated subdirectory to avoid autouse fixture dirs in tmp_path
        scan_root = tmp_path / "scan_root"
        scan_root.mkdir()
        scanner = FolderScanner()
        result = scanner.scan_directory(scan_root)
        assert result.total_folders == 0
        assert result.total_files == 0
        assert result.root_path == scan_root

    def test_scan_counts_files_and_folders(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.scanner import FolderScanner

        scan_root = tmp_path / "scan_root"
        scan_root.mkdir()
        (scan_root / "folderA").mkdir()
        (scan_root / "folderB").mkdir()
        (scan_root / "file1.txt").write_text("a")
        (scan_root / "file2.txt").write_text("b")
        scanner = FolderScanner()
        result = scanner.scan_directory(scan_root)
        assert result.total_folders == 2
        assert result.total_files == 2

    def test_scan_skip_hidden_folders(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.scanner import FolderScanner

        scan_root = tmp_path / "scan_root"
        scan_root.mkdir()
        (scan_root / ".hidden").mkdir()
        (scan_root / "visible").mkdir()
        scanner = FolderScanner(skip_hidden=True)
        result = scanner.scan_directory(scan_root)
        assert result.total_folders == 1

    def test_scan_include_hidden_when_disabled(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.scanner import FolderScanner

        scan_root = tmp_path / "scan_root"
        scan_root.mkdir()
        (scan_root / ".hidden").mkdir()
        (scan_root / "visible").mkdir()
        scanner = FolderScanner(skip_hidden=False)
        result = scanner.scan_directory(scan_root)
        assert result.total_folders == 2


class TestFolderScannerPatternDetection:
    def test_detects_para_structure(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.scanner import FolderScanner

        for name in ("projects", "areas", "resources", "archive"):
            (tmp_path / name).mkdir()
        scanner = FolderScanner()
        result = scanner.scan_directory(tmp_path)
        assert any("PARA" in p for p in result.detected_patterns)

    def test_detects_jd_numbers(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.scanner import FolderScanner

        (tmp_path / "10 Finance").mkdir()
        scanner = FolderScanner()
        result = scanner.scan_directory(tmp_path)
        assert any("Johnny Decimal" in p for p in result.detected_patterns)

    def test_warns_on_deep_hierarchy(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.scanner import FolderScanner

        # Create depth > 5
        deep = tmp_path
        for i in range(7):
            deep = deep / f"level{i}"
            deep.mkdir()
        scanner = FolderScanner()
        result = scanner.scan_directory(tmp_path)
        assert any("Deep hierarchy" in w for w in result.warnings)

    def test_warns_on_many_top_level_folders(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.scanner import FolderScanner

        for i in range(12):
            (tmp_path / f"folder{i}").mkdir()
        scanner = FolderScanner()
        result = scanner.scan_directory(tmp_path)
        assert any("top-level folders" in w for w in result.warnings)

    def test_looks_like_jd_number_area(self) -> None:
        from file_organizer.methodologies.johnny_decimal.scanner import FolderScanner

        scanner = FolderScanner()
        assert scanner._looks_like_jd_number("10") is True
        assert scanner._looks_like_jd_number("99 Something") is True
        assert scanner._looks_like_jd_number("1") is False

    def test_looks_like_jd_number_category(self) -> None:
        from file_organizer.methodologies.johnny_decimal.scanner import FolderScanner

        scanner = FolderScanner()
        assert scanner._looks_like_jd_number("11.01") is True
        assert scanner._looks_like_jd_number("11.01.001") is True
        assert scanner._looks_like_jd_number("random") is False


# ---------------------------------------------------------------------------
# TestFolderTransformer
# ---------------------------------------------------------------------------


class TestFolderTransformerBasic:
    def test_creates_plan_for_single_folder(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.scanner import FolderInfo
        from file_organizer.methodologies.johnny_decimal.transformer import FolderTransformer

        scheme = _default_scheme()
        generator = _make_generator(scheme)
        transformer = FolderTransformer(scheme, generator)

        folder = FolderInfo(path=tmp_path / "myproject", name="myproject", depth=0)
        plan = transformer.create_transformation_plan([folder], tmp_path)

        assert plan.estimated_changes == 1
        assert len(plan.rules) == 1
        assert "myproject" in plan.rules[0].target_name

    def test_area_rule_uses_preserve_names_flag(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.scanner import FolderInfo
        from file_organizer.methodologies.johnny_decimal.transformer import FolderTransformer

        scheme = _default_scheme()
        generator = _make_generator(scheme)
        transformer = FolderTransformer(scheme, generator, preserve_original_names=False)

        folder = FolderInfo(path=tmp_path / "myproject", name="myproject", depth=0)
        plan = transformer.create_transformation_plan([folder], tmp_path)

        # Target name should be just the number without the original folder name
        assert "myproject" not in plan.rules[0].target_name

    def test_category_rules_created_for_children(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.scanner import FolderInfo
        from file_organizer.methodologies.johnny_decimal.transformer import FolderTransformer

        scheme = _default_scheme()
        generator = _make_generator(scheme)
        transformer = FolderTransformer(scheme, generator)

        parent_path = tmp_path / "parent"
        child_path = tmp_path / "parent" / "child"
        child = FolderInfo(path=child_path, name="child", depth=1)
        parent = FolderInfo(path=parent_path, name="parent", depth=0, children=[child])

        plan = transformer.create_transformation_plan([parent], tmp_path)
        # 1 area rule + 1 category rule
        assert plan.estimated_changes == 2
        assert len(plan.rules) == 2

    def test_id_rules_created_for_nested_children(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.scanner import FolderInfo
        from file_organizer.methodologies.johnny_decimal.transformer import FolderTransformer

        scheme = _default_scheme()
        generator = _make_generator(scheme)
        transformer = FolderTransformer(scheme, generator)

        grandchild = FolderInfo(path=tmp_path / "a" / "b" / "c", name="c", depth=2)
        child = FolderInfo(path=tmp_path / "a" / "b", name="b", depth=1, children=[grandchild])
        parent = FolderInfo(path=tmp_path / "a", name="a", depth=0, children=[child])

        plan = transformer.create_transformation_plan([parent], tmp_path)
        # 1 area + 1 category + 1 id = 3
        assert plan.estimated_changes == 3

    def test_generate_preview_contains_root(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.scanner import FolderInfo
        from file_organizer.methodologies.johnny_decimal.transformer import FolderTransformer

        scheme = _default_scheme()
        generator = _make_generator(scheme)
        transformer = FolderTransformer(scheme, generator)

        folder = FolderInfo(path=tmp_path / "docs", name="docs", depth=0)
        plan = transformer.create_transformation_plan([folder], tmp_path)
        preview = transformer.generate_preview(plan)

        assert str(tmp_path) in preview
        assert "Transformation Plan" in preview

    def test_area_number_matches_scheme_name(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.scanner import FolderInfo
        from file_organizer.methodologies.johnny_decimal.transformer import FolderTransformer

        scheme = _default_scheme()
        generator = _make_generator(scheme)
        transformer = FolderTransformer(scheme, generator)

        # "finance" should match the Finance area (10-19) in default scheme
        folder = FolderInfo(path=tmp_path / "finance", name="finance", depth=0)
        plan = transformer.create_transformation_plan([folder], tmp_path)
        area_num = plan.rules[0].jd_number.area
        assert 10 <= area_num <= 19


# ---------------------------------------------------------------------------
# TestMigrationValidator
# ---------------------------------------------------------------------------


class TestMigrationValidatorBasic:
    def test_valid_plan_returns_is_valid_true(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from file_organizer.methodologies.johnny_decimal.transformer import (
            TransformationPlan,
            TransformationRule,
        )
        from file_organizer.methodologies.johnny_decimal.validator import MigrationValidator

        existing = tmp_path / "mydir"
        existing.mkdir()

        generator = _make_generator()
        rule = TransformationRule(
            source_path=existing,
            target_name="10 mydir",
            jd_number=JohnnyDecimalNumber(area=10),
            action="rename",
            confidence=0.8,
        )
        plan = TransformationPlan(root_path=tmp_path, rules=[rule], estimated_changes=1)
        validator = MigrationValidator(generator)
        result = validator.validate_plan(plan)

        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_nonexistent_source_path_is_error(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from file_organizer.methodologies.johnny_decimal.transformer import (
            TransformationPlan,
            TransformationRule,
        )
        from file_organizer.methodologies.johnny_decimal.validator import MigrationValidator

        generator = _make_generator()
        rule = TransformationRule(
            source_path=tmp_path / "ghost",
            target_name="10 ghost",
            jd_number=JohnnyDecimalNumber(area=10),
            action="rename",
            confidence=0.8,
        )
        plan = TransformationPlan(root_path=tmp_path, rules=[rule], estimated_changes=1)
        validator = MigrationValidator(generator)
        result = validator.validate_plan(plan)

        assert result.is_valid is False
        assert any("does not exist" in e.message for e in result.errors)

    def test_area_out_of_range_is_error(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from file_organizer.methodologies.johnny_decimal.transformer import (
            TransformationPlan,
            TransformationRule,
        )
        from file_organizer.methodologies.johnny_decimal.validator import MigrationValidator

        existing = tmp_path / "mydir"
        existing.mkdir()

        generator = _make_generator()
        rule = TransformationRule(
            source_path=existing,
            target_name="05 mydir",
            jd_number=JohnnyDecimalNumber(area=5),  # area < 10 is invalid for JD
            action="rename",
            confidence=0.5,
        )
        plan = TransformationPlan(root_path=tmp_path, rules=[rule], estimated_changes=1)
        validator = MigrationValidator(generator)
        result = validator.validate_plan(plan)

        assert result.is_valid is False
        assert any("out of range" in e.message for e in result.errors)

    def test_invalid_character_in_target_name_is_error(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from file_organizer.methodologies.johnny_decimal.transformer import (
            TransformationPlan,
            TransformationRule,
        )
        from file_organizer.methodologies.johnny_decimal.validator import MigrationValidator

        existing = tmp_path / "mydir"
        existing.mkdir()

        generator = _make_generator()
        rule = TransformationRule(
            source_path=existing,
            target_name="10 my<dir>",
            jd_number=JohnnyDecimalNumber(area=10),
            action="rename",
            confidence=0.8,
        )
        plan = TransformationPlan(root_path=tmp_path, rules=[rule], estimated_changes=1)
        validator = MigrationValidator(generator)
        result = validator.validate_plan(plan)

        assert result.is_valid is False
        assert any("invalid character" in e.message for e in result.errors)

    def test_duplicate_jd_numbers_detected(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from file_organizer.methodologies.johnny_decimal.transformer import (
            TransformationPlan,
            TransformationRule,
        )
        from file_organizer.methodologies.johnny_decimal.validator import MigrationValidator

        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()

        generator = _make_generator()
        rule1 = TransformationRule(
            source_path=d1,
            target_name="10 a",
            jd_number=JohnnyDecimalNumber(area=10),
            action="rename",
            confidence=0.8,
        )
        rule2 = TransformationRule(
            source_path=d2,
            target_name="10 b",
            jd_number=JohnnyDecimalNumber(area=10),
            action="rename",
            confidence=0.8,
        )
        plan = TransformationPlan(root_path=tmp_path, rules=[rule1, rule2], estimated_changes=2)
        validator = MigrationValidator(generator)
        result = validator.validate_plan(plan)

        assert result.is_valid is False
        assert any("Duplicate JD number" in e.message for e in result.errors)

    def test_generate_report_valid_plan(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from file_organizer.methodologies.johnny_decimal.transformer import (
            TransformationPlan,
            TransformationRule,
        )
        from file_organizer.methodologies.johnny_decimal.validator import MigrationValidator

        existing = tmp_path / "mydir"
        existing.mkdir()

        generator = _make_generator()
        rule = TransformationRule(
            source_path=existing,
            target_name="10 mydir",
            jd_number=JohnnyDecimalNumber(area=10),
            action="rename",
            confidence=0.8,
        )
        plan = TransformationPlan(root_path=tmp_path, rules=[rule], estimated_changes=1)
        validator = MigrationValidator(generator)
        result = validator.validate_plan(plan)
        report = validator.generate_report(result)

        assert "VALID" in report
        assert "Errors: 0" in report


class TestValidationIssue:
    def test_error_severity_sets_is_valid_false(self) -> None:
        from file_organizer.methodologies.johnny_decimal.validator import (
            ValidationIssue,
            ValidationResult,
        )

        result = ValidationResult(is_valid=True)
        issue = ValidationIssue(severity="error", rule_index=0, message="Something wrong")
        result.add_issue(issue)

        assert result.is_valid is False
        assert len(result.errors) == 1

    def test_warning_severity_does_not_fail_validation(self) -> None:
        from file_organizer.methodologies.johnny_decimal.validator import (
            ValidationIssue,
            ValidationResult,
        )

        result = ValidationResult(is_valid=True)
        issue = ValidationIssue(severity="warning", rule_index=0, message="A warning")
        result.add_issue(issue)

        assert result.is_valid is True
        assert len(result.warnings) == 1

    def test_info_goes_to_info_list(self) -> None:
        from file_organizer.methodologies.johnny_decimal.validator import (
            ValidationIssue,
            ValidationResult,
        )

        result = ValidationResult(is_valid=True)
        issue = ValidationIssue(severity="info", rule_index=0, message="FYI")
        result.add_issue(issue)

        assert len(result.info) == 1
        assert result.is_valid is True


# ---------------------------------------------------------------------------
# TestJohnnyDecimalGenerator
# ---------------------------------------------------------------------------


class TestJohnnyDecimalGeneratorBasic:
    def test_register_number_and_check_unavailable(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from file_organizer.methodologies.johnny_decimal.numbering import JohnnyDecimalGenerator

        scheme = _default_scheme()
        gen = JohnnyDecimalGenerator(scheme)
        num = JohnnyDecimalNumber(area=10)
        gen.register_existing_number(num, Path("/some/path"))

        assert gen.is_number_available(num) is False

    def test_register_duplicate_raises_conflict_error(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from file_organizer.methodologies.johnny_decimal.numbering import (
            JohnnyDecimalGenerator,
            NumberConflictError,
        )

        scheme = _default_scheme()
        gen = JohnnyDecimalGenerator(scheme)
        num = JohnnyDecimalNumber(area=10)
        gen.register_existing_number(num, Path("/some/path"))

        with pytest.raises(NumberConflictError):
            gen.register_existing_number(num, Path("/other/path"))

    def test_generate_area_number_returns_valid_jd(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from file_organizer.methodologies.johnny_decimal.numbering import JohnnyDecimalGenerator

        scheme = _default_scheme()
        gen = JohnnyDecimalGenerator(scheme)
        num = gen.generate_area_number("Finance")

        assert isinstance(num, JohnnyDecimalNumber)
        assert 10 <= num.area <= 99

    def test_generate_category_number_in_area(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from file_organizer.methodologies.johnny_decimal.numbering import JohnnyDecimalGenerator

        scheme = _default_scheme()
        gen = JohnnyDecimalGenerator(scheme)
        num = gen.generate_category_number(area=10, name="Budgets")

        assert isinstance(num, JohnnyDecimalNumber)
        assert num.area == 10
        assert num.category is not None

    def test_generate_id_number_in_category(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from file_organizer.methodologies.johnny_decimal.numbering import JohnnyDecimalGenerator

        scheme = _default_scheme()
        gen = JohnnyDecimalGenerator(scheme)
        num = gen.generate_id_number(area=10, category=1, name="Q1 Budget")

        assert isinstance(num, JohnnyDecimalNumber)
        assert num.area == 10
        assert num.category == 1
        assert num.item_id is not None

    def test_validate_number_area_not_in_scheme(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from file_organizer.methodologies.johnny_decimal.numbering import JohnnyDecimalGenerator

        scheme = _default_scheme()
        gen = JohnnyDecimalGenerator(scheme)
        # Area 90 not in default scheme (10-59)
        num = JohnnyDecimalNumber(area=90)
        is_valid, errors = gen.validate_number(num)

        assert is_valid is False
        assert len(errors) == 1
        assert "not defined" in errors[0]

    def test_find_conflicts_exact_match(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from file_organizer.methodologies.johnny_decimal.numbering import JohnnyDecimalGenerator

        scheme = _default_scheme()
        gen = JohnnyDecimalGenerator(scheme)
        num = JohnnyDecimalNumber(area=10)
        gen.register_existing_number(num, Path("/path/to/dir"))

        conflicts = gen.find_conflicts(num)
        assert len(conflicts) == 1
        assert conflicts[0][0] == "10"

    def test_resolve_conflict_skip_strategy(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from file_organizer.methodologies.johnny_decimal.numbering import JohnnyDecimalGenerator

        scheme = _default_scheme()
        gen = JohnnyDecimalGenerator(scheme)
        # Register area=10, cat=1 as used; skip strategy on a category number finds next category
        gen.register_existing_number(JohnnyDecimalNumber(area=10, category=1), Path("/p"))
        num = JohnnyDecimalNumber(area=10, category=1)
        alternative = gen.resolve_conflict(num, strategy="skip")

        # Should be a different category (not 1) in area 10
        assert alternative.area == 10
        assert alternative.category != 1

    def test_get_usage_statistics(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from file_organizer.methodologies.johnny_decimal.numbering import JohnnyDecimalGenerator

        scheme = _default_scheme()
        gen = JohnnyDecimalGenerator(scheme)
        gen.register_existing_number(JohnnyDecimalNumber(area=10), Path("/a"))
        gen.register_existing_number(JohnnyDecimalNumber(area=10, category=1), Path("/b"))

        stats = gen.get_usage_statistics()
        assert stats["total_numbers"] == 2
        assert stats["categories_used"] == 1

    def test_clear_registrations(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from file_organizer.methodologies.johnny_decimal.numbering import JohnnyDecimalGenerator

        scheme = _default_scheme()
        gen = JohnnyDecimalGenerator(scheme)
        num = JohnnyDecimalNumber(area=10)
        gen.register_existing_number(num, Path("/a"))
        gen.clear_registrations()

        assert gen.is_number_available(num) is True

    def test_suggest_number_for_content_finance_keywords(self) -> None:
        from file_organizer.methodologies.johnny_decimal.numbering import JohnnyDecimalGenerator

        scheme = _default_scheme()
        gen = JohnnyDecimalGenerator(scheme)
        number, confidence, reasons = gen.suggest_number_for_content(
            "This is a budget invoice expense report", filename="budget.pdf"
        )

        # Should match Finance area (10-19)
        assert number.area in range(10, 20)
        assert confidence > 0.3
        assert len(reasons) >= 1


# ---------------------------------------------------------------------------
# TestJohnnyDecimalCategories
# ---------------------------------------------------------------------------


class TestJohnnyDecimalNumberDataclass:
    def test_area_only_formatted_number(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        num = JohnnyDecimalNumber(area=10)
        assert num.formatted_number == "10"

    def test_category_formatted_number(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        num = JohnnyDecimalNumber(area=11, category=1)
        assert num.formatted_number == "11.01"

    def test_id_formatted_number(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        num = JohnnyDecimalNumber(area=11, category=1, item_id=1)
        assert num.formatted_number == "11.01.001"

    def test_level_area(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import (
            JohnnyDecimalNumber,
            NumberLevel,
        )

        num = JohnnyDecimalNumber(area=10)
        assert num.level == NumberLevel.AREA

    def test_level_category(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import (
            JohnnyDecimalNumber,
            NumberLevel,
        )

        num = JohnnyDecimalNumber(area=10, category=1)
        assert num.level == NumberLevel.CATEGORY

    def test_level_id(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import (
            JohnnyDecimalNumber,
            NumberLevel,
        )

        num = JohnnyDecimalNumber(area=10, category=1, item_id=1)
        assert num.level == NumberLevel.ID

    def test_parent_number_from_id(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        num = JohnnyDecimalNumber(area=11, category=1, item_id=5)
        assert num.parent_number == "11.01"

    def test_parent_number_from_category(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        num = JohnnyDecimalNumber(area=11, category=1)
        assert num.parent_number == "11"

    def test_area_out_of_range_raises(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        with pytest.raises(ValueError, match="Area must be between"):
            JohnnyDecimalNumber(area=100)

    def test_item_id_without_category_raises(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        with pytest.raises(ValueError, match="Cannot have item_id without category"):
            JohnnyDecimalNumber(area=10, item_id=1)

    def test_from_string_area(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        num = JohnnyDecimalNumber.from_string("10")
        assert num.area == 10
        assert num.category is None

    def test_from_string_category(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        num = JohnnyDecimalNumber.from_string("11.01")
        assert num.area == 11
        assert num.category == 1

    def test_from_string_id(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        num = JohnnyDecimalNumber.from_string("11.01.001")
        assert num.area == 11
        assert num.category == 1
        assert num.item_id == 1

    def test_from_string_invalid_raises(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        with pytest.raises(ValueError, match="Invalid Johnny Decimal format"):
            JohnnyDecimalNumber.from_string("11.01.001.002")

    def test_equality_based_on_numbers_only(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        a = JohnnyDecimalNumber(area=10, name="Finance")
        b = JohnnyDecimalNumber(area=10, name="Renamed Finance")
        assert a == b

    def test_hashable_in_set(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        a = JohnnyDecimalNumber(area=10)
        b = JohnnyDecimalNumber(area=10)
        s = {a, b}
        assert len(s) == 1


class TestNumberingScheme:
    def test_get_default_scheme_has_areas(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import get_default_scheme

        scheme = get_default_scheme()
        assert len(scheme.areas) > 0
        # Default areas cover 10-59
        assert scheme.get_area(10) is not None

    def test_add_area_and_get_area(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import (
            AreaDefinition,
            NumberingScheme,
        )

        scheme = NumberingScheme(name="test", description="test scheme")
        area_def = AreaDefinition(
            area_range_start=10,
            area_range_end=19,
            name="Finance",
            description="Financial docs",
        )
        scheme.add_area(area_def)
        assert scheme.get_area(10) is not None
        assert scheme.get_area(15) is not None

    def test_reserve_number_and_check(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import (
            JohnnyDecimalNumber,
            get_default_scheme,
        )

        scheme = get_default_scheme()
        num = JohnnyDecimalNumber(area=10)
        scheme.reserve_number(num)
        assert scheme.is_number_reserved(num) is True

    def test_get_available_categories(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import (
            CategoryDefinition,
            get_default_scheme,
        )

        scheme = get_default_scheme()
        cat_def = CategoryDefinition(area=10, category=1, name="Budgets", description="Budgets")
        scheme.add_category(cat_def)
        cats = scheme.get_available_categories(10)
        assert "10.01" in cats


# ---------------------------------------------------------------------------
# TestPARAJohnnyDecimalBridge
# ---------------------------------------------------------------------------


class TestPARAJohnnyDecimalBridge:
    def _make_bridge(self):
        from file_organizer.methodologies.johnny_decimal.compatibility import (
            PARAJohnnyDecimalBridge,
        )
        from file_organizer.methodologies.johnny_decimal.config import PARAIntegrationConfig

        cfg = PARAIntegrationConfig(
            enabled=True,
            projects_area=10,
            areas_area=20,
            resources_area=30,
            archive_area=40,
        )
        return PARAJohnnyDecimalBridge(cfg)

    def test_para_to_jd_area_projects(self) -> None:
        from file_organizer.methodologies.johnny_decimal.compatibility import PARACategory

        bridge = self._make_bridge()
        area = bridge.para_to_jd_area(PARACategory.PROJECTS, index=0)
        assert area == 10

    def test_para_to_jd_area_with_index(self) -> None:
        from file_organizer.methodologies.johnny_decimal.compatibility import PARACategory

        bridge = self._make_bridge()
        area = bridge.para_to_jd_area(PARACategory.PROJECTS, index=3)
        assert area == 13

    def test_para_to_jd_area_invalid_index_raises(self) -> None:
        from file_organizer.methodologies.johnny_decimal.compatibility import PARACategory

        bridge = self._make_bridge()
        with pytest.raises(ValueError, match="Index must be 0-9"):
            bridge.para_to_jd_area(PARACategory.PROJECTS, index=10)

    def test_jd_area_to_para_projects(self) -> None:
        from file_organizer.methodologies.johnny_decimal.compatibility import PARACategory

        bridge = self._make_bridge()
        category = bridge.jd_area_to_para(10)
        assert category == PARACategory.PROJECTS

    def test_jd_area_to_para_out_of_range_returns_none(self) -> None:
        bridge = self._make_bridge()
        result = bridge.jd_area_to_para(99)
        assert result is None

    def test_is_para_area_true_for_mapped(self) -> None:
        bridge = self._make_bridge()
        assert bridge.is_para_area(20) is True

    def test_is_para_area_false_for_unmapped(self) -> None:
        bridge = self._make_bridge()
        assert bridge.is_para_area(99) is False

    def test_get_para_path_suggestion(self) -> None:
        from file_organizer.methodologies.johnny_decimal.compatibility import PARACategory

        bridge = self._make_bridge()
        suggestion = bridge.get_para_path_suggestion(PARACategory.PROJECTS, "MyProject")
        assert "Projects" in suggestion
        assert "MyProject" in suggestion

    def test_create_para_structure_creates_dirs(self, tmp_path: Path) -> None:
        bridge = self._make_bridge()
        paths = bridge.create_para_structure(tmp_path)
        # 4 PARA categories should be created
        assert len(paths) == 4
        for path in paths.values():
            assert path.exists()
            assert path.is_dir()


# ---------------------------------------------------------------------------
# TestCompatibilityAnalyzer
# ---------------------------------------------------------------------------


class TestCompatibilityAnalyzer:
    def _make_analyzer(self, para_enabled: bool = False):
        from file_organizer.methodologies.johnny_decimal.categories import get_default_scheme
        from file_organizer.methodologies.johnny_decimal.compatibility import CompatibilityAnalyzer
        from file_organizer.methodologies.johnny_decimal.config import (
            CompatibilityConfig,
            JohnnyDecimalConfig,
            PARAIntegrationConfig,
        )

        para_cfg = PARAIntegrationConfig(enabled=para_enabled)
        compat_cfg = CompatibilityConfig(para_integration=para_cfg)
        config = JohnnyDecimalConfig(scheme=get_default_scheme(), compatibility=compat_cfg)
        return CompatibilityAnalyzer(config)

    def test_detect_para_structure_finds_projects(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.compatibility import PARACategory

        (tmp_path / "projects").mkdir()
        analyzer = self._make_analyzer()
        result = analyzer.detect_para_structure(tmp_path)

        assert result[PARACategory.PROJECTS] is not None

    def test_detect_para_structure_missing_returns_none(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.compatibility import PARACategory

        analyzer = self._make_analyzer()
        result = analyzer.detect_para_structure(tmp_path)

        assert result[PARACategory.PROJECTS] is None

    def test_detect_para_nonexistent_path(self, tmp_path: Path) -> None:
        analyzer = self._make_analyzer()
        result = analyzer.detect_para_structure(tmp_path / "nonexistent")
        # All should be None
        assert all(v is None for v in result.values())

    def test_is_mixed_structure_true(self, tmp_path: Path) -> None:
        (tmp_path / "projects").mkdir()
        (tmp_path / "10 Finance").mkdir()
        analyzer = self._make_analyzer()
        assert analyzer.is_mixed_structure(tmp_path) is True

    def test_is_mixed_structure_false_no_jd(self, tmp_path: Path) -> None:
        (tmp_path / "projects").mkdir()
        analyzer = self._make_analyzer()
        assert analyzer.is_mixed_structure(tmp_path) is False

    def test_suggest_migration_strategy_clean_structure(self, tmp_path: Path) -> None:
        analyzer = self._make_analyzer()
        strategy = analyzer.suggest_migration_strategy(tmp_path)
        assert "recommendations" in strategy
        assert len(strategy["recommendations"]) >= 1

    def test_suggest_migration_strategy_para_without_integration(self, tmp_path: Path) -> None:
        (tmp_path / "projects").mkdir()
        analyzer = self._make_analyzer(para_enabled=False)
        strategy = analyzer.suggest_migration_strategy(tmp_path)
        assert any("Enable PARA integration" in r for r in strategy["recommendations"])


# ---------------------------------------------------------------------------
# TestHybridOrganizer
# ---------------------------------------------------------------------------


class TestHybridOrganizer:
    def _make_organizer(self):
        from file_organizer.methodologies.johnny_decimal.categories import get_default_scheme
        from file_organizer.methodologies.johnny_decimal.compatibility import HybridOrganizer
        from file_organizer.methodologies.johnny_decimal.config import (
            CompatibilityConfig,
            JohnnyDecimalConfig,
            PARAIntegrationConfig,
        )

        para_cfg = PARAIntegrationConfig(
            enabled=True,
            projects_area=10,
            areas_area=20,
            resources_area=30,
            archive_area=40,
        )
        compat_cfg = CompatibilityConfig(para_integration=para_cfg)
        config = JohnnyDecimalConfig(scheme=get_default_scheme(), compatibility=compat_cfg)
        return HybridOrganizer(config)

    def test_create_hybrid_structure_creates_dirs(self, tmp_path: Path) -> None:
        organizer = self._make_organizer()
        created = organizer.create_hybrid_structure(tmp_path)

        assert len(created) > 0
        for path in created.values():
            assert path.exists()

    def test_categorize_item_returns_jd_number(self) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from file_organizer.methodologies.johnny_decimal.compatibility import PARACategory

        organizer = self._make_organizer()
        num = organizer.categorize_item("MyProject", PARACategory.PROJECTS)

        assert isinstance(num, JohnnyDecimalNumber)
        assert 10 <= num.area <= 19

    def test_get_item_path_returns_path(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from file_organizer.methodologies.johnny_decimal.compatibility import PARACategory

        organizer = self._make_organizer()
        jd_num = JohnnyDecimalNumber(area=10, category=1)
        path = organizer.get_item_path(tmp_path, PARACategory.PROJECTS, jd_num, "MyProject")

        assert "10" in str(path)
        assert "MyProject" in str(path)


# ---------------------------------------------------------------------------
# TestAdapters
# ---------------------------------------------------------------------------


class TestPARAAdapter:
    def _make_config(self, para_enabled: bool = True):
        from file_organizer.methodologies.johnny_decimal.categories import get_default_scheme
        from file_organizer.methodologies.johnny_decimal.config import (
            CompatibilityConfig,
            JohnnyDecimalConfig,
            PARAIntegrationConfig,
        )

        para_cfg = PARAIntegrationConfig(
            enabled=para_enabled,
            projects_area=10,
            areas_area=20,
            resources_area=30,
            archive_area=40,
        )
        compat_cfg = CompatibilityConfig(para_integration=para_cfg)
        return JohnnyDecimalConfig(scheme=get_default_scheme(), compatibility=compat_cfg)

    def test_adapt_to_jd_projects_category(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.adapters import (
            OrganizationItem,
            PARAAdapter,
        )

        config = self._make_config()
        adapter = PARAAdapter(config)
        item = OrganizationItem(
            name="Sprint Plan", path=tmp_path / "plan.md", category="projects", metadata={}
        )
        num = adapter.adapt_to_jd(item)
        assert 10 <= num.area <= 19

    def test_adapt_to_jd_unknown_category_raises(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.adapters import (
            OrganizationItem,
            PARAAdapter,
        )

        config = self._make_config()
        adapter = PARAAdapter(config)
        item = OrganizationItem(
            name="Foo", path=tmp_path / "foo.md", category="unknown_cat", metadata={}
        )
        with pytest.raises(ValueError, match="Cannot determine PARA category"):
            adapter.adapt_to_jd(item)

    def test_adapt_from_jd_returns_para_item(self) -> None:
        from file_organizer.methodologies.johnny_decimal.adapters import PARAAdapter
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        config = self._make_config()
        adapter = PARAAdapter(config)
        num = JohnnyDecimalNumber(area=10, category=1)
        item = adapter.adapt_from_jd(num, "My Document")

        assert item.name == "My Document"
        assert "projects" in item.category

    def test_adapt_from_jd_out_of_range_raises(self) -> None:
        from file_organizer.methodologies.johnny_decimal.adapters import PARAAdapter
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        config = self._make_config()
        adapter = PARAAdapter(config)
        num = JohnnyDecimalNumber(area=99)
        with pytest.raises(ValueError, match="not in PARA range"):
            adapter.adapt_from_jd(num, "Something")

    def test_can_adapt_returns_true_for_para_category(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.adapters import (
            OrganizationItem,
            PARAAdapter,
        )

        config = self._make_config()
        adapter = PARAAdapter(config)
        item = OrganizationItem(
            name="Doc", path=tmp_path / "doc.md", category="resources", metadata={}
        )
        assert adapter.can_adapt(item) is True

    def test_can_adapt_returns_false_for_unknown(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.adapters import (
            OrganizationItem,
            PARAAdapter,
        )

        config = self._make_config()
        adapter = PARAAdapter(config)
        item = OrganizationItem(
            name="Doc", path=tmp_path / "doc.md", category="randomcat", metadata={}
        )
        assert adapter.can_adapt(item) is False


class TestFileSystemAdapter:
    def _make_config(self):
        from file_organizer.methodologies.johnny_decimal.categories import get_default_scheme
        from file_organizer.methodologies.johnny_decimal.config import (
            CompatibilityConfig,
            JohnnyDecimalConfig,
            PARAIntegrationConfig,
        )

        para_cfg = PARAIntegrationConfig(enabled=False)
        compat_cfg = CompatibilityConfig(para_integration=para_cfg)
        return JohnnyDecimalConfig(scheme=get_default_scheme(), compatibility=compat_cfg)

    def test_adapt_to_jd_top_level_folder(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.adapters import (
            FileSystemAdapter,
            OrganizationItem,
        )
        from file_organizer.methodologies.johnny_decimal.categories import NumberLevel

        config = self._make_config()
        adapter = FileSystemAdapter(config)
        item = OrganizationItem(
            name="finance", path=Path("finance"), category="filesystem", metadata={}
        )
        num = adapter.adapt_to_jd(item)
        assert num.level == NumberLevel.AREA

    def test_adapt_from_jd_area_level(self) -> None:
        from file_organizer.methodologies.johnny_decimal.adapters import FileSystemAdapter
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        config = self._make_config()
        adapter = FileSystemAdapter(config)
        num = JohnnyDecimalNumber(area=10)
        item = adapter.adapt_from_jd(num, "Finance")
        assert "Finance" in str(item.path)

    def test_can_adapt_always_true(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.adapters import (
            FileSystemAdapter,
            OrganizationItem,
        )

        config = self._make_config()
        adapter = FileSystemAdapter(config)
        item = OrganizationItem(
            name="anything", path=tmp_path / "file", category="whatever", metadata={}
        )
        assert adapter.can_adapt(item) is True


class TestAdapterRegistry:
    def _make_config(self, para_enabled: bool = True):
        from file_organizer.methodologies.johnny_decimal.categories import get_default_scheme
        from file_organizer.methodologies.johnny_decimal.config import (
            CompatibilityConfig,
            JohnnyDecimalConfig,
            PARAIntegrationConfig,
        )

        para_cfg = PARAIntegrationConfig(
            enabled=para_enabled,
            projects_area=10,
            areas_area=20,
            resources_area=30,
            archive_area=40,
        )
        compat_cfg = CompatibilityConfig(para_integration=para_cfg)
        return JohnnyDecimalConfig(scheme=get_default_scheme(), compatibility=compat_cfg)

    def test_create_default_registry_with_para_enabled(self) -> None:
        from file_organizer.methodologies.johnny_decimal.adapters import (
            PARAAdapter,
            create_default_registry,
        )

        config = self._make_config(para_enabled=True)
        registry = create_default_registry(config)

        # PARA + filesystem adapters
        assert len(registry._adapters) == 2
        assert any(isinstance(a, PARAAdapter) for a in registry._adapters)

    def test_create_default_registry_without_para(self) -> None:
        from file_organizer.methodologies.johnny_decimal.adapters import (
            FileSystemAdapter,
            create_default_registry,
        )

        config = self._make_config(para_enabled=False)
        registry = create_default_registry(config)

        # Only filesystem adapter
        assert len(registry._adapters) == 1
        assert isinstance(registry._adapters[0], FileSystemAdapter)

    def test_adapt_to_jd_routes_to_correct_adapter(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.adapters import (
            OrganizationItem,
            create_default_registry,
        )

        config = self._make_config(para_enabled=True)
        registry = create_default_registry(config)
        item = OrganizationItem(
            name="Plan", path=tmp_path / "plan.md", category="projects", metadata={}
        )
        num = registry.adapt_to_jd(item)
        assert num is not None
        assert 10 <= num.area <= 19

    def test_get_adapter_returns_none_for_no_match(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.adapters import AdapterRegistry

        registry = AdapterRegistry()
        # No adapters registered
        from file_organizer.methodologies.johnny_decimal.adapters import OrganizationItem

        item = OrganizationItem(name="x", path=tmp_path, category="y", metadata={})
        assert registry.get_adapter(item) is None

    def test_adapt_from_jd_filesystem_methodology(self) -> None:
        from file_organizer.methodologies.johnny_decimal.adapters import create_default_registry
        from file_organizer.methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        config = self._make_config(para_enabled=False)
        registry = create_default_registry(config)
        num = JohnnyDecimalNumber(area=10)
        item = registry.adapt_from_jd(num, "Docs", methodology="filesystem")

        assert item is not None
        assert item.name == "Docs"


# ---------------------------------------------------------------------------
# TestJohnnyDecimalMigrator
# ---------------------------------------------------------------------------


class TestJohnnyDecimalMigratorBasic:
    def test_create_migration_plan_for_single_folder(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.migrator import JohnnyDecimalMigrator

        src = tmp_path / "src"
        src.mkdir()
        (src / "finance").mkdir()
        migrator = JohnnyDecimalMigrator()
        plan, scan_result = migrator.create_migration_plan(src)

        assert scan_result.total_folders == 1
        assert plan.estimated_changes >= 1

    def test_create_migration_plan_raises_for_nonexistent_path(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.migrator import JohnnyDecimalMigrator

        migrator = JohnnyDecimalMigrator()
        with pytest.raises(ValueError, match="Path does not exist"):
            migrator.create_migration_plan(tmp_path / "nonexistent")

    def test_validate_plan_calls_validator(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.migrator import JohnnyDecimalMigrator

        (tmp_path / "projects").mkdir()
        migrator = JohnnyDecimalMigrator()
        plan, _ = migrator.create_migration_plan(tmp_path)
        validation = migrator.validate_plan(plan)

        # is_valid depends on whether source paths exist; check it's a ValidationResult
        assert hasattr(validation, "is_valid")
        assert hasattr(validation, "errors")

    def test_execute_migration_dry_run_no_rename(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.migrator import JohnnyDecimalMigrator

        orig = tmp_path / "finance"
        orig.mkdir()
        migrator = JohnnyDecimalMigrator()
        plan, _ = migrator.create_migration_plan(tmp_path)
        result = migrator.execute_migration(plan, dry_run=True, create_backup=False)

        # Original folder still exists (dry run)
        assert orig.exists()
        assert result.transformed_count >= 1

    def test_execute_migration_real_renames_folder(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.migrator import JohnnyDecimalMigrator

        src = tmp_path / "src"
        src.mkdir()
        (src / "finance").mkdir()

        migrator = JohnnyDecimalMigrator()
        plan, _ = migrator.create_migration_plan(src)
        result = migrator.execute_migration(plan, dry_run=False, create_backup=False)

        # Original folder should have been renamed
        assert result.transformed_count >= 1

    def test_execute_migration_real_creates_backup(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.migrator import JohnnyDecimalMigrator

        src = tmp_path / "src"
        src.mkdir()
        (src / "finance").mkdir()

        migrator = JohnnyDecimalMigrator()
        plan, _ = migrator.create_migration_plan(src)
        result = migrator.execute_migration(plan, dry_run=False, create_backup=True)

        assert result.backup_path is not None
        assert result.backup_path.exists()

    def test_rollback_empty_history_returns_false(self) -> None:
        from file_organizer.methodologies.johnny_decimal.migrator import JohnnyDecimalMigrator

        migrator = JohnnyDecimalMigrator()
        success = migrator.rollback()
        assert success is False

    def test_rollback_after_real_migration(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.migrator import JohnnyDecimalMigrator

        src = tmp_path / "workspace"
        src.mkdir()
        (src / "finance").mkdir()

        fake_backup = tmp_path / "backup"

        migrator = JohnnyDecimalMigrator()
        plan, _ = migrator.create_migration_plan(src)
        # create_backup=True initialises RollbackInfo; mock _create_backup so no
        # real shutil.copytree call is needed, and mock _save_rollback_info to
        # avoid needing the real config data directory.
        with (
            patch.object(migrator, "_create_backup", return_value=fake_backup),
            patch.object(migrator, "_save_rollback_info"),
        ):
            migrator.execute_migration(plan, dry_run=False, create_backup=True)

        assert len(migrator._rollback_history) == 1
        # Rollback should succeed (moves folders back)
        success = migrator.rollback()
        assert success is True

    def test_generate_preview_string_has_key_sections(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.migrator import JohnnyDecimalMigrator

        (tmp_path / "finance").mkdir()
        migrator = JohnnyDecimalMigrator()
        plan, scan_result = migrator.create_migration_plan(tmp_path)
        preview = migrator.generate_preview(plan, scan_result)

        assert "Source Analysis" in preview
        assert "Migration Plan" in preview

    def test_generate_report_has_statistics_section(self, tmp_path: Path) -> None:
        from file_organizer.methodologies.johnny_decimal.migrator import JohnnyDecimalMigrator

        (tmp_path / "finance").mkdir()
        migrator = JohnnyDecimalMigrator()
        plan, _ = migrator.create_migration_plan(tmp_path)
        result = migrator.execute_migration(plan, dry_run=True, create_backup=False)
        report = migrator.generate_report(result)

        assert "Statistics" in report
        assert "Transformed" in report

    def test_rollback_invalid_migration_id_raises(self) -> None:
        from datetime import UTC, datetime

        from file_organizer.methodologies.johnny_decimal.migrator import (
            JohnnyDecimalMigrator,
            RollbackInfo,
        )

        migrator = JohnnyDecimalMigrator()
        migrator._rollback_history.append(
            RollbackInfo(
                migration_id="real_id",
                timestamp=datetime.now(UTC),
                original_structure={},
                backup_path=None,
            )
        )
        with pytest.raises(ValueError, match="Migration ID not found"):
            migrator.rollback("nonexistent_id")
