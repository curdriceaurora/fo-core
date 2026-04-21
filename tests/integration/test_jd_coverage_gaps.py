"""Integration tests targeting specific coverage gaps in the Johnny Decimal module.

Each test class maps to a source file and exercises branches/statements that
are reachable in integration scenarios but were absent from prior test suites.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from methodologies.johnny_decimal.categories import NumberingScheme
    from methodologies.johnny_decimal.compatibility import JohnnyDecimalConfig
    from methodologies.johnny_decimal.numbering import JohnnyDecimalGenerator
    from methodologies.johnny_decimal.system import JohnnyDecimalSystem
    from methodologies.johnny_decimal.transformer import TransformationPlan, TransformationRule

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# categories.py — lines 37, 117, 193, 195, 231, 233, 282
# ---------------------------------------------------------------------------


class TestCategoriesGaps:
    """Integration tests for categories.py data model branches."""

    def test_number_level_str(self) -> None:
        from methodologies.johnny_decimal.categories import NumberLevel

        assert str(NumberLevel.AREA) == "Area"
        assert str(NumberLevel.CATEGORY) == "Category"
        assert str(NumberLevel.ID) == "Id"

    def test_jd_number_str_with_name(self) -> None:
        from methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        n = JohnnyDecimalNumber(area=10, category=1, name="Finance")
        result = str(n)
        assert "Finance" in result

    def test_area_definition_post_init_valid(self) -> None:
        from methodologies.johnny_decimal.categories import AreaDefinition

        area = AreaDefinition(
            area_range_start=10,
            area_range_end=19,
            name="Finance",
            description="Financial documents",
        )
        assert area.area_range_start == 10
        assert area.area_range_end == 19

    def test_category_definition_post_init_valid(self) -> None:
        from methodologies.johnny_decimal.categories import CategoryDefinition

        cat = CategoryDefinition(area=10, category=1, name="Budgets", description="Budget docs")
        assert cat.area == 10
        assert cat.category == 1

    def test_numbering_result_str_path_coercion(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.categories import JohnnyDecimalNumber, NumberingResult

        result = NumberingResult(
            file_path=str(tmp_path / "report.pdf"),  # type: ignore[arg-type]
            number=JohnnyDecimalNumber(area=10, category=1),
            confidence=0.8,
            reasons=["test reason"],
        )
        assert result.file_path == tmp_path / "report.pdf"


# ---------------------------------------------------------------------------
# numbering.py — lines 89, 415-416, 422-423
# ---------------------------------------------------------------------------


class TestNumberingGaps:
    """Integration tests for numbering.py conflict and availability branches."""

    def _make_scheme_and_generator(self) -> tuple[NumberingScheme, JohnnyDecimalGenerator]:
        from methodologies.johnny_decimal.categories import (
            AreaDefinition,
            CategoryDefinition,
            NumberingScheme,
        )
        from methodologies.johnny_decimal.numbering import JohnnyDecimalGenerator

        scheme = NumberingScheme(name="Test", description="Test scheme")
        area = AreaDefinition(10, 19, "Finance", "Finance")
        scheme.add_area(area)
        cat = CategoryDefinition(area=10, category=1, name="Budgets", description="Budgets")
        scheme.add_category(cat)
        return scheme, JohnnyDecimalGenerator(scheme)

    def test_is_number_available_reserved(self) -> None:
        from methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        scheme, gen = self._make_scheme_and_generator()
        num = JohnnyDecimalNumber(area=10, category=5)
        scheme.reserve_number(num)
        assert gen.is_number_available(num) is False

    def test_find_conflicts_parent_conflict(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        _, gen = self._make_scheme_and_generator()
        area_num = JohnnyDecimalNumber(area=10)
        gen.register_existing_number(area_num, tmp_path / "10 Finance")
        # Now look for conflicts on a category under that area
        cat_num = JohnnyDecimalNumber(area=10, category=1)
        conflicts = gen.find_conflicts(cat_num)
        parent_strs = [c[0] for c in conflicts]
        assert "10" in parent_strs

    def test_find_conflicts_child_conflict(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        _, gen = self._make_scheme_and_generator()
        cat_num = JohnnyDecimalNumber(area=10, category=1)
        gen.register_existing_number(cat_num, tmp_path / "10.01 Budgets")
        # Look for conflicts on the parent area
        area_num = JohnnyDecimalNumber(area=10)
        conflicts = gen.find_conflicts(area_num)
        child_strs = [c[0] for c in conflicts]
        assert any(s.startswith("10.") for s in child_strs)


# ---------------------------------------------------------------------------
# system.py — lines 83, 161-170, 190-192, 289, 323-327, 487-497
# ---------------------------------------------------------------------------


class TestSystemGaps:
    """Integration tests for system.py orchestration and conflict branches."""

    def _make_system(self) -> JohnnyDecimalSystem:
        from methodologies.johnny_decimal.categories import AreaDefinition, NumberingScheme
        from methodologies.johnny_decimal.system import JohnnyDecimalSystem

        scheme = NumberingScheme(name="Test", description="Test")
        area = AreaDefinition(10, 19, "Finance", "Finance")
        scheme.add_area(area)
        return JohnnyDecimalSystem(scheme=scheme)

    def test_assign_number_with_conflicting_preferred(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        jd_sys = self._make_system()
        preferred = JohnnyDecimalNumber(area=10, category=1)
        # Register the preferred number first to create a conflict
        f1 = tmp_path / "file1.txt"
        f1.write_text("content")
        jd_sys.assign_number_to_file(f1, preferred_number=preferred)
        # Assign a second file with same preferred → triggers conflict resolution
        f2 = tmp_path / "file2.txt"
        f2.write_text("content")
        result = jd_sys.assign_number_to_file(f2, preferred_number=preferred)
        # Conflict resolution must produce a different number than the preferred
        assert result.number.formatted_number != preferred.formatted_number

    def test_assign_number_with_content(self, tmp_path: Path) -> None:
        jd_sys = self._make_system()
        f = tmp_path / "budget_report.txt"
        f.write_text("annual budget")
        result = jd_sys.assign_number_to_file(f, content="annual budget finance")
        assert result.number is not None
        assert result.confidence > 0

    def test_get_area_summary(self) -> None:
        jd_sys = self._make_system()
        summary = jd_sys.get_area_summary(10)
        assert summary["area"] == 10
        assert summary["name"] == "Finance"

    def test_get_all_areas_summary(self) -> None:
        jd_sys = self._make_system()
        summaries = jd_sys.get_all_areas_summary()
        # _make_system registers area 10-19: 10 individual area numbers
        assert len(summaries) == 10
        area_numbers = [s["area"] for s in summaries]
        assert 10 in area_numbers
        assert 19 in area_numbers

    def test_initialize_from_directory_skips_conflict(self, tmp_path: Path) -> None:
        # Two folders that both parse to area 10 — second triggers NumberConflictError
        (tmp_path / "10 Finance").mkdir()
        (tmp_path / "10 Duplicate").mkdir()
        jd_sys = self._make_system()
        jd_sys.initialize_from_directory(tmp_path)
        assert jd_sys._initialized is True

    def test_renumber_file(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        jd_sys = self._make_system()
        f = tmp_path / "doc.txt"
        f.write_text("content")
        old = JohnnyDecimalNumber(area=10, category=1)
        new = JohnnyDecimalNumber(area=10, category=2)
        jd_sys.assign_number_to_file(f, preferred_number=old)
        result = jd_sys.renumber_file(old, new, f)
        assert result.number.category == 2

    def test_reserve_number_range_category_level(self) -> None:
        from methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        jd_sys = self._make_system()
        start = JohnnyDecimalNumber(area=10, category=5)
        end = JohnnyDecimalNumber(area=10, category=7)
        jd_sys.reserve_number_range(start, end)
        assert not jd_sys.generator.is_number_available(JohnnyDecimalNumber(area=10, category=6))

    def test_reserve_number_range_id_level(self) -> None:
        from methodologies.johnny_decimal.categories import JohnnyDecimalNumber

        jd_sys = self._make_system()
        start = JohnnyDecimalNumber(area=10, category=1, item_id=1)
        end = JohnnyDecimalNumber(area=10, category=1, item_id=3)
        jd_sys.reserve_number_range(start, end)
        assert not jd_sys.generator.is_number_available(
            JohnnyDecimalNumber(area=10, category=1, item_id=2)
        )


# ---------------------------------------------------------------------------
# validator.py — lines 138, 166, 179, 206-221, 254, 265, 286, 315-347
# ---------------------------------------------------------------------------


class TestValidatorGaps:
    """Integration tests for validator.py validation rule branches."""

    def _make_generator(self) -> JohnnyDecimalGenerator:
        from methodologies.johnny_decimal.categories import AreaDefinition, NumberingScheme
        from methodologies.johnny_decimal.numbering import JohnnyDecimalGenerator

        scheme = NumberingScheme(name="Test", description="Test")
        scheme.add_area(AreaDefinition(10, 19, "Finance", "Finance"))
        return JohnnyDecimalGenerator(scheme)

    def _make_plan(self, tmp_path: Path, rules: list[TransformationRule]) -> TransformationPlan:
        from methodologies.johnny_decimal.transformer import TransformationPlan

        return TransformationPlan(
            root_path=tmp_path,
            rules=rules,
            estimated_changes=len(rules),
        )

    def _make_rule(
        self, source: Path, target: str, area: int, category: int | None = None
    ) -> TransformationRule:
        from methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from methodologies.johnny_decimal.transformer import TransformationRule

        return TransformationRule(
            source_path=source,
            target_name=target,
            jd_number=JohnnyDecimalNumber(area=area, category=category),
            action="rename",
            confidence=0.9,
            reasoning=["test"],
        )

    def test_number_already_in_use(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from methodologies.johnny_decimal.validator import MigrationValidator

        gen = self._make_generator()
        src = tmp_path / "Finance"
        src.mkdir()
        already_used = JohnnyDecimalNumber(area=10, category=1)
        gen.register_existing_number(already_used, tmp_path / "other.txt")

        rule = self._make_rule(src, "10.01 Finance", area=10, category=1)
        plan = self._make_plan(tmp_path, [rule])
        result = MigrationValidator(gen).validate_plan(plan)
        assert any("already in use" in i.message for i in result.errors)

    def test_duplicate_target_name(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.validator import MigrationValidator

        gen = self._make_generator()
        src1 = tmp_path / "Finance"
        src2 = tmp_path / "Budget"
        src1.mkdir()
        src2.mkdir()
        rule1 = self._make_rule(src1, "same-name", area=10, category=1)
        rule2 = self._make_rule(src2, "same-name", area=10, category=2)
        plan = self._make_plan(tmp_path, [rule1, rule2])
        result = MigrationValidator(gen).validate_plan(plan)
        assert any("Duplicate target name" in i.message for i in result.errors)

    def test_target_path_exists_conflict(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.validator import MigrationValidator

        gen = self._make_generator()
        src = tmp_path / "Finance"
        src.mkdir()
        existing = tmp_path / "existing-target"
        existing.mkdir()
        rule = self._make_rule(src, "existing-target", area=10, category=1)
        plan = self._make_plan(tmp_path, [rule])
        result = MigrationValidator(gen).validate_plan(plan)
        assert any("already exists" in i.message for i in result.warnings)

    def test_out_of_range_area(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from methodologies.johnny_decimal.transformer import TransformationRule
        from methodologies.johnny_decimal.validator import MigrationValidator

        gen = self._make_generator()
        src = tmp_path / "Bad"
        src.mkdir()
        rule = TransformationRule(
            source_path=src,
            target_name="00 Bad",
            jd_number=JohnnyDecimalNumber(area=0),  # area 0 is out of range (must be 10-99)
            action="rename",
            confidence=0.5,
            reasoning=["test"],
        )
        plan = self._make_plan(tmp_path, [rule])
        result = MigrationValidator(gen).validate_plan(plan)
        assert any("out of range" in i.message.lower() for i in result.errors)

    def test_problematic_chars_in_target_name(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.validator import MigrationValidator

        gen = self._make_generator()
        src = tmp_path / "Finance"
        src.mkdir()
        rule = self._make_rule(src, "10.01 Finance/Sub", area=10, category=1)
        plan = self._make_plan(tmp_path, [rule])
        result = MigrationValidator(gen).validate_plan(plan)
        assert any("invalid character" in i.message.lower() for i in result.errors)

    def test_very_long_target_name(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.validator import MigrationValidator

        gen = self._make_generator()
        src = tmp_path / "Finance"
        src.mkdir()
        long_name = "10.01 " + "x" * 210
        rule = self._make_rule(src, long_name, area=10, category=1)
        plan = self._make_plan(tmp_path, [rule])
        result = MigrationValidator(gen).validate_plan(plan)
        assert any("very long" in i.message.lower() for i in result.warnings)

    def test_nested_transformation_conflict(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.validator import MigrationValidator

        gen = self._make_generator()
        parent = tmp_path / "Finance"
        child = parent / "Budgets"
        parent.mkdir()
        child.mkdir()
        rule_parent = self._make_rule(parent, "10 Finance", area=10)
        rule_child = self._make_rule(child, "10.01 Budgets", area=10, category=1)
        plan = self._make_plan(tmp_path, [rule_parent, rule_child])
        result = MigrationValidator(gen).validate_plan(plan)
        assert any("Nested" in i.message for i in result.warnings)

    def test_generate_report_with_issues(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.validator import MigrationValidator

        gen = self._make_generator()
        src = tmp_path / "Bad"
        src.mkdir()
        rule = self._make_rule(src, "10.01 Bad/Slash", area=10, category=1)
        plan = self._make_plan(tmp_path, [rule])
        validator = MigrationValidator(gen)
        result = validator.validate_plan(plan)
        report = validator.generate_report(result)
        assert "Errors" in report or "ERRORS" in report or "❌" in report


# ---------------------------------------------------------------------------
# transformer.py — lines 98-101, 107, 185, 239, 303-308, 332-341
# ---------------------------------------------------------------------------


class TestTransformerGaps:
    """Integration tests for transformer.py plan generation branches."""

    def _make_scheme_and_generator(self) -> tuple[NumberingScheme, JohnnyDecimalGenerator]:
        from methodologies.johnny_decimal.categories import (
            AreaDefinition,
            CategoryDefinition,
            NumberingScheme,
        )
        from methodologies.johnny_decimal.numbering import JohnnyDecimalGenerator

        scheme = NumberingScheme(name="Test", description="Test")
        scheme.add_area(AreaDefinition(10, 19, "Finance", "Finance"))
        cat = CategoryDefinition(area=10, category=1, name="Budgets", description="Budgets")
        scheme.add_category(cat)
        return scheme, JohnnyDecimalGenerator(scheme)

    def test_preserve_original_names_false(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.scanner import FolderInfo
        from methodologies.johnny_decimal.transformer import FolderTransformer

        scheme, gen = self._make_scheme_and_generator()
        transformer = FolderTransformer(scheme=scheme, generator=gen, preserve_original_names=False)
        folder = FolderInfo(path=tmp_path / "Finance", name="Finance", depth=0)
        plan = transformer.create_transformation_plan([folder], tmp_path)
        rule = plan.rules[0]
        assert "Finance" not in rule.target_name

    def test_preserve_original_names_true(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.scanner import FolderInfo
        from methodologies.johnny_decimal.transformer import FolderTransformer

        scheme, gen = self._make_scheme_and_generator()
        transformer = FolderTransformer(scheme=scheme, generator=gen, preserve_original_names=True)
        folder = FolderInfo(path=tmp_path / "Finance", name="Finance", depth=0)
        plan = transformer.create_transformation_plan([folder], tmp_path)
        rule = plan.rules[0]
        assert "Finance" in rule.target_name

    def test_category_rules_with_children(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.scanner import FolderInfo
        from methodologies.johnny_decimal.transformer import FolderTransformer

        scheme, gen = self._make_scheme_and_generator()
        transformer = FolderTransformer(scheme=scheme, generator=gen, preserve_original_names=True)
        child = FolderInfo(path=tmp_path / "Finance" / "Budgets", name="Budgets", depth=1)
        folder = FolderInfo(path=tmp_path / "Finance", name="Finance", depth=0, children=[child])
        plan = transformer.create_transformation_plan([folder], tmp_path)
        assert len(plan.rules) >= 2

    def test_scheme_matched_category_suggestion(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.scanner import FolderInfo
        from methodologies.johnny_decimal.transformer import FolderTransformer

        scheme, gen = self._make_scheme_and_generator()
        transformer = FolderTransformer(scheme=scheme, generator=gen, preserve_original_names=True)
        child = FolderInfo(path=tmp_path / "Finance" / "Budgets", name="Budgets", depth=1)
        folder = FolderInfo(path=tmp_path / "Finance", name="Finance", depth=0, children=[child])
        plan = transformer.create_transformation_plan([folder], tmp_path)
        cat_rules = [r for r in plan.rules if r.jd_number.category is not None]
        assert any(r.jd_number.category == 1 for r in cat_rules)

    def test_generate_preview_with_warnings_and_conflicts(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.transformer import (
            FolderTransformer,
            TransformationPlan,
        )

        scheme, gen = self._make_scheme_and_generator()
        transformer = FolderTransformer(scheme=scheme, generator=gen)
        plan = TransformationPlan(
            root_path=tmp_path,
            rules=[],
            estimated_changes=0,
            conflicts=["conflict one"],
            warnings=["warning one"],
        )
        preview = transformer.generate_preview(plan)
        assert "Warnings" in preview
        assert "Conflicts" in preview


# ---------------------------------------------------------------------------
# migrator.py — lines 163-165, 207-210, 261-262, 286-288, 392, 421, 436-441
# ---------------------------------------------------------------------------


class TestMigratorGaps:
    """Integration tests for migrator.py execution and rollback branches."""

    def _setup_directory(self, tmp_path: Path) -> Path:
        """Create a minimal directory structure for migration tests."""
        root = tmp_path / "root"
        root.mkdir()
        (root / "Finance").mkdir()
        (root / "Finance" / "report.txt").write_text("budget report")
        (root / "Projects").mkdir()
        return root

    def test_execute_migration_with_backup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from methodologies.johnny_decimal.migrator import JohnnyDecimalMigrator

        monkeypatch.setattr(
            "methodologies.johnny_decimal.migrator._get_data_dir",
            lambda: tmp_path / "data",
        )
        root = self._setup_directory(tmp_path)
        migrator = JohnnyDecimalMigrator()
        plan, _ = migrator.create_migration_plan(root)
        result = migrator.execute_migration(plan, dry_run=False, create_backup=True)
        assert result is not None
        assert result.backup_path is not None  # create_backup=True must produce a backup

    def test_rollback_after_migration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from methodologies.johnny_decimal.migrator import JohnnyDecimalMigrator

        monkeypatch.setattr(
            "methodologies.johnny_decimal.migrator._get_data_dir",
            lambda: tmp_path / "data",
        )
        root = self._setup_directory(tmp_path)
        migrator = JohnnyDecimalMigrator()
        plan, _ = migrator.create_migration_plan(root)
        migrator.execute_migration(plan, dry_run=False, create_backup=True)
        success = migrator.rollback()
        assert success is True
        # Original folder names should be restored after rollback
        assert (root / "Finance").exists()

    def test_generate_report(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.migrator import JohnnyDecimalMigrator

        root = self._setup_directory(tmp_path)
        migrator = JohnnyDecimalMigrator()
        plan, _ = migrator.create_migration_plan(root)
        result = migrator.execute_migration(plan, dry_run=True)
        report = migrator.generate_report(result)
        assert isinstance(report, str)
        assert len(report) > 0

    def test_generate_preview(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.migrator import JohnnyDecimalMigrator

        root = self._setup_directory(tmp_path)
        migrator = JohnnyDecimalMigrator()
        plan, scan = migrator.create_migration_plan(root)
        validation = migrator.validate_plan(plan)
        preview = migrator.generate_preview(plan, scan, validation)
        assert "# Johnny Decimal Migration Preview" in preview
        assert "## Migration Plan" in preview


# ---------------------------------------------------------------------------
# compatibility.py — lines 203, 226, 243, 250-252, 272-284, 291, 415, 419
# ---------------------------------------------------------------------------


class TestCompatibilityGaps:
    """Integration tests for compatibility.py PARA detection and hybrid branches."""

    def _make_config(self) -> JohnnyDecimalConfig:
        from methodologies.johnny_decimal.categories import NumberingScheme
        from methodologies.johnny_decimal.compatibility import JohnnyDecimalConfig

        scheme = NumberingScheme(name="Test", description="Test")
        return JohnnyDecimalConfig(scheme=scheme)

    def test_detect_para_structure(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.compatibility import CompatibilityAnalyzer, PARACategory

        (tmp_path / "Projects").mkdir()
        (tmp_path / "Areas").mkdir()
        (tmp_path / "Resources").mkdir()
        (tmp_path / "Archive").mkdir()
        analyzer = CompatibilityAnalyzer(self._make_config())
        result = analyzer.detect_para_structure(tmp_path)
        assert PARACategory.PROJECTS in result
        assert result[PARACategory.PROJECTS] is not None

    def test_is_mixed_structure(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.compatibility import CompatibilityAnalyzer

        (tmp_path / "Projects").mkdir()
        (tmp_path / "10 Finance").mkdir()
        analyzer = CompatibilityAnalyzer(self._make_config())
        result = analyzer.is_mixed_structure(tmp_path)
        assert result is True

    def test_suggest_migration_strategy(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.compatibility import CompatibilityAnalyzer

        (tmp_path / "Projects").mkdir()
        (tmp_path / "Areas").mkdir()
        analyzer = CompatibilityAnalyzer(self._make_config())
        strategy = analyzer.suggest_migration_strategy(tmp_path)
        assert "recommendations" in strategy
        assert "detected_para" in strategy
        assert "is_mixed_structure" in strategy

    def test_create_para_structure(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.compatibility import (
            PARAIntegrationConfig,
            PARAJohnnyDecimalBridge,
        )

        config = PARAIntegrationConfig()
        bridge = PARAJohnnyDecimalBridge(config)
        bridge.create_para_structure(tmp_path)
        # create_para_structure names folders "<area_num> <Category>"
        assert (tmp_path / "10 Projects").exists()
        assert (tmp_path / "20 Areas").exists()
        assert (tmp_path / "30 Resources").exists()
        assert (tmp_path / "40 Archive").exists()

    def test_hybrid_organizer_get_item_path_all_levels(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.categories import JohnnyDecimalNumber
        from methodologies.johnny_decimal.compatibility import (
            HybridOrganizer,
            PARACategory,
        )

        config = self._make_config()
        para_cfg = config.compatibility.para_integration
        organizer = HybridOrganizer(config)
        # Area-level: path ends with zero-padded area number
        area_num = JohnnyDecimalNumber(area=para_cfg.projects_area)
        p1 = organizer.get_item_path(tmp_path, PARACategory.PROJECTS, area_num)
        assert str(p1).endswith(f"{para_cfg.projects_area:02d}")
        # Category-level: path contains "area.category" segment
        cat_num = JohnnyDecimalNumber(area=para_cfg.projects_area, category=1)
        p2 = organizer.get_item_path(tmp_path, PARACategory.PROJECTS, cat_num)
        assert f"{para_cfg.projects_area:02d}.01" in str(p2)
        # ID-level: path contains full ID and item name
        id_num = JohnnyDecimalNumber(area=para_cfg.projects_area, category=1, item_id=1)
        p3 = organizer.get_item_path(tmp_path, PARACategory.PROJECTS, id_num, item_name="MyDoc")
        assert "MyDoc" in str(p3)


# ---------------------------------------------------------------------------
# scanner.py — lines 159-168, 188-191, 248-258, 266-274, 361
# ---------------------------------------------------------------------------


class TestScannerGaps:
    """Integration tests for scanner.py directory traversal branches."""

    def test_scan_skips_hidden_files(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.scanner import FolderScanner

        (tmp_path / "Finance").mkdir()
        (tmp_path / ".hidden").mkdir()
        (tmp_path / "Finance" / "report.pdf").write_text("x")
        scanner = FolderScanner(skip_hidden=True)
        result = scanner.scan_directory(tmp_path)
        names = [f.name for f in result.folder_tree]
        assert ".hidden" not in names

    def test_scan_accumulates_file_size(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.scanner import FolderScanner

        folder = tmp_path / "Finance"
        folder.mkdir()
        (folder / "a.txt").write_text("hello")  # 5 bytes
        (folder / "b.txt").write_text("world")  # 5 bytes
        scanner = FolderScanner(skip_hidden=False)
        result = scanner.scan_directory(tmp_path)
        finance = next((f for f in result.folder_tree if f.name == "Finance"), None)
        assert finance is not None
        assert finance.file_count == 2
        assert finance.total_size >= 10  # both files are 5 bytes each

    def test_scan_detects_jd_pattern(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.scanner import FolderScanner

        (tmp_path / "10 Finance").mkdir()
        (tmp_path / "10 Duplicate").mkdir()
        scanner = FolderScanner()
        result = scanner.scan_directory(tmp_path)
        assert "Existing Johnny Decimal numbers detected" in result.detected_patterns

    def test_scan_deep_hierarchy_warning(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.scanner import FolderScanner

        # Create 7 levels of nesting (depth 0-6) to trigger max_depth > 5 warning
        deep = tmp_path
        for i in range(7):
            deep = deep / f"level{i}"
            deep.mkdir()
        scanner = FolderScanner(max_depth=10)
        result = scanner.scan_directory(tmp_path)
        assert result.max_depth >= 6
        assert any("Deep hierarchy" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# config.py — lines 146, 214, 350-351
# ---------------------------------------------------------------------------


class TestConfigGaps:
    """Integration tests for config.py builder and persistence branches."""

    def test_config_builder_with_categories(self) -> None:
        from methodologies.johnny_decimal.config import ConfigBuilder

        config = (
            ConfigBuilder(scheme_name="MyScheme")
            .add_area(10, "Finance", "Finance area")
            .add_category(10, 1, "Budgets", "Budget docs")
            .build()
        )
        assert config.scheme.get_category(10, 1) is not None

    def test_config_builder_custom_mapping(self) -> None:
        from methodologies.johnny_decimal.config import ConfigBuilder

        builder = ConfigBuilder()
        config = builder.add_custom_mapping("finance", 10).build()
        assert config.custom_mappings.get("finance") == 10

    def test_save_and_load_configuration(self, tmp_path: Path) -> None:
        from methodologies.johnny_decimal.categories import (
            AreaDefinition,
            JohnnyDecimalNumber,
            NumberingScheme,
        )
        from methodologies.johnny_decimal.system import JohnnyDecimalSystem

        scheme = NumberingScheme(name="Persist", description="Persistence test")
        scheme.add_area(AreaDefinition(10, 19, "Finance", "Finance"))
        config_file = tmp_path / "config.json"
        jd_sys = JohnnyDecimalSystem(scheme=scheme, config_path=config_file)
        # Register a number so we can verify round-trip persistence
        registered_num = JohnnyDecimalNumber(area=10, category=1)
        jd_sys.generator.register_existing_number(registered_num, tmp_path / "budget.txt")
        jd_sys.save_configuration()
        assert config_file.exists()

        sys2 = JohnnyDecimalSystem(scheme=scheme, config_path=config_file)
        sys2.load_configuration()
        assert sys2._initialized is True
        assert sys2.scheme.get_area(10) is not None
        assert "10.01" in sys2.generator._number_mappings
