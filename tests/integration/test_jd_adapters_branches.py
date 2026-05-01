"""Integration tests for methodologies/johnny_decimal/adapters.py.

Covers uncovered branches:
- PARAAdapter.adapt_to_jd: non-int subcategory fallback (line 136)
- FileSystemAdapter.adapt_to_jd: custom_mappings hit (lines 211-212)
- FileSystemAdapter.adapt_to_jd: depth-2 and depth-3 paths (lines 221-231)
- FileSystemAdapter.adapt_from_jd: CATEGORY and ID level paths (lines 246-256)
- FileSystemAdapter._suggest_area_from_name: numeric-prefix path (lines 274-276)
- FileSystemAdapter._suggest_category_from_name: XX.XX format path (lines 285-295)
- FileSystemAdapter._suggest_id_from_index: boundary (line 299)
- AdapterRegistry.get_adapter: no adapter found (line 333)
- AdapterRegistry.adapt_to_jd: adapter found (line 347) and not found
- AdapterRegistry.adapt_from_jd: para/filesystem paths (lines 365-369)
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def default_config():
    from methodologies.johnny_decimal.config import create_default_config

    return create_default_config()


@pytest.fixture
def para_config():
    from methodologies.johnny_decimal.config import create_para_compatible_config

    return create_para_compatible_config()


@pytest.fixture
def fs_adapter(default_config):
    from methodologies.johnny_decimal.adapters import FileSystemAdapter

    return FileSystemAdapter(default_config)


@pytest.fixture
def para_adapter(para_config):
    from methodologies.johnny_decimal.adapters import PARAAdapter

    return PARAAdapter(para_config)


def _item(name: str, path: str, category: str = "general", **meta) -> object:
    from methodologies.johnny_decimal.adapters import OrganizationItem

    return OrganizationItem(name=name, path=Path(path), category=category, metadata=meta)


# ---------------------------------------------------------------------------
# PARAAdapter — non-int subcategory fallback (line 136)
# ---------------------------------------------------------------------------


@pytest.mark.ci
def test_para_adapt_to_jd_non_int_subcategory_falls_back_to_one(para_adapter) -> None:
    """adapt_to_jd falls back to subcategory=1 when metadata value is not an int."""
    item = _item(
        "doc.pdf",
        "Projects/doc.pdf",
        category="projects",
        subcategory="not-an-int",  # triggers line 136
    )
    result = para_adapter.adapt_to_jd(item)
    assert result.category == 1


@pytest.mark.ci
def test_para_adapt_to_jd_int_subcategory_used(para_adapter) -> None:
    """adapt_to_jd uses int subcategory from metadata directly."""
    item = _item(
        "doc.pdf",
        "Projects/doc.pdf",
        category="projects",
        subcategory=5,
    )
    result = para_adapter.adapt_to_jd(item)
    assert result.category == 5


# ---------------------------------------------------------------------------
# PARAAdapter — adapt_from_jd and can_adapt False path (lines 285-295, 299)
# ---------------------------------------------------------------------------


@pytest.mark.ci
def test_para_adapt_from_jd_returns_organization_item(para_adapter) -> None:
    """adapt_from_jd constructs an OrganizationItem in the PARA folder."""
    from methodologies.johnny_decimal.categories import JohnnyDecimalNumber

    # Area 10 = Projects in default PARA mapping
    jd_num = JohnnyDecimalNumber(area=10, category=1)
    result = para_adapter.adapt_from_jd(jd_num, "my_doc.pdf")

    assert result.name == "my_doc.pdf"
    assert "jd_number" in result.metadata
    assert "para_category" in result.metadata


@pytest.mark.ci
def test_para_can_adapt_returns_false_for_unknown_category(para_adapter) -> None:
    """can_adapt returns False when category doesn't match any PARA value (line 299)."""
    item = _item("file.txt", "unknown/file.txt", category="zzzunknown")
    assert para_adapter.can_adapt(item) is False


@pytest.mark.ci
def test_para_adapt_to_jd_raises_for_unknown_category(para_adapter) -> None:
    """adapt_to_jd raises ValueError when category can't be resolved to a PARA category."""
    item = _item("file.txt", "unknown/file.txt", category="zzzunknown")
    with pytest.raises(ValueError, match="Cannot determine PARA category"):
        para_adapter.adapt_to_jd(item)


# ---------------------------------------------------------------------------
# FileSystemAdapter — adapt_to_jd: custom mapping (lines 211-212)
# ---------------------------------------------------------------------------


@pytest.mark.ci
def test_fs_adapt_to_jd_custom_mapping(default_config) -> None:
    """adapt_to_jd uses custom_mappings when item name matches (lines 211-212)."""
    from methodologies.johnny_decimal.adapters import FileSystemAdapter

    default_config.custom_mappings["projects"] = 42
    adapter = FileSystemAdapter(default_config)
    item = _item("projects", "projects")
    result = adapter.adapt_to_jd(item)
    assert result.area == 42


# ---------------------------------------------------------------------------
# FileSystemAdapter — adapt_to_jd: depth-2 and depth-3 (lines 221-231)
# ---------------------------------------------------------------------------


@pytest.mark.ci
def test_fs_adapt_to_jd_depth_2_returns_category_level(fs_adapter) -> None:
    """depth-2 path assigns area+category (lines 221-225)."""
    from methodologies.johnny_decimal.categories import NumberLevel

    item = _item("subfolder", "parent/subfolder")
    result = fs_adapter.adapt_to_jd(item)
    assert result.level == NumberLevel.CATEGORY


@pytest.mark.ci
def test_fs_adapt_to_jd_depth_3_returns_id_level(fs_adapter) -> None:
    """depth-3 path assigns area+category+id (lines 226-231)."""
    from methodologies.johnny_decimal.categories import NumberLevel

    item = _item("file.txt", "parent/sub/file.txt")
    result = fs_adapter.adapt_to_jd(item)
    assert result.level == NumberLevel.ID


# ---------------------------------------------------------------------------
# FileSystemAdapter — adapt_from_jd: CATEGORY and ID paths (lines 246-256)
# ---------------------------------------------------------------------------


@pytest.mark.ci
def test_fs_adapt_from_jd_category_level(fs_adapter) -> None:
    """adapt_from_jd constructs a two-part path for CATEGORY level (lines 246-249)."""
    from methodologies.johnny_decimal.categories import JohnnyDecimalNumber, NumberLevel

    jd_num = JohnnyDecimalNumber(area=10, category=2)
    assert jd_num.level == NumberLevel.CATEGORY
    result = fs_adapter.adapt_from_jd(jd_num, "My Category")
    # Path should be: "10 Area/10.02 My Category"
    assert len(result.path.parts) == 2
    assert result.name == "My Category"


@pytest.mark.ci
def test_fs_adapt_from_jd_id_level(fs_adapter) -> None:
    """adapt_from_jd constructs a three-part path for ID level (lines 250-256)."""
    from methodologies.johnny_decimal.categories import JohnnyDecimalNumber, NumberLevel

    jd_num = JohnnyDecimalNumber(area=10, category=2, item_id=5)
    assert jd_num.level == NumberLevel.ID
    result = fs_adapter.adapt_from_jd(jd_num, "My Item")
    assert len(result.path.parts) == 3
    assert result.name == "My Item"


# ---------------------------------------------------------------------------
# FileSystemAdapter — _suggest_area_from_name: numeric prefix path (274-276)
# ---------------------------------------------------------------------------


@pytest.mark.ci
def test_suggest_area_from_name_numeric_prefix(fs_adapter) -> None:
    """When name starts with a number in 10-99, that number is returned (lines 274-276)."""
    result = fs_adapter._suggest_area_from_name("20 Finance")
    assert result == 20


@pytest.mark.ci
def test_suggest_area_from_name_hash_fallback(fs_adapter) -> None:
    """When no numeric prefix, MD5-based hash is used as fallback."""
    result = fs_adapter._suggest_area_from_name("Documents")
    assert 10 <= result <= 99


# ---------------------------------------------------------------------------
# FileSystemAdapter — _suggest_category_from_name: XX.XX format (285-295)
# ---------------------------------------------------------------------------


@pytest.mark.ci
def test_suggest_category_from_name_dot_format(fs_adapter) -> None:
    """XX.XX prefix extracts category number (lines 285-292)."""
    result = fs_adapter._suggest_category_from_name("10.05 My Category")
    assert result == 5


@pytest.mark.ci
def test_suggest_category_from_name_hash_fallback(fs_adapter) -> None:
    """No dot format → MD5 hash-based fallback (lines 293-295)."""
    result = fs_adapter._suggest_category_from_name("SomeName")
    assert 1 <= result <= 99


# ---------------------------------------------------------------------------
# FileSystemAdapter — _suggest_id_from_index boundary (line 299)
# ---------------------------------------------------------------------------


@pytest.mark.ci
def test_suggest_id_from_index_normal(fs_adapter) -> None:
    """_suggest_id_from_index returns index+1 for normal values (line 299)."""
    assert fs_adapter._suggest_id_from_index(0) == 1
    assert fs_adapter._suggest_id_from_index(4) == 5


@pytest.mark.ci
def test_suggest_id_from_index_clamps_to_999(fs_adapter) -> None:
    """Large index is clamped to 999."""
    assert fs_adapter._suggest_id_from_index(9999) == 999


# ---------------------------------------------------------------------------
# AdapterRegistry — no adapter found, adapt_to_jd, adapt_from_jd (lines 331-369)
# ---------------------------------------------------------------------------


@pytest.mark.ci
def test_registry_get_adapter_none_when_empty() -> None:
    """get_adapter returns None when no adapters are registered (line 333)."""
    from methodologies.johnny_decimal.adapters import AdapterRegistry

    registry = AdapterRegistry()
    item = _item("file.txt", "file.txt")
    assert registry.get_adapter(item) is None


@pytest.mark.ci
def test_registry_adapt_to_jd_returns_none_when_no_adapter() -> None:
    """adapt_to_jd returns None when no adapter can handle the item."""
    from methodologies.johnny_decimal.adapters import AdapterRegistry

    registry = AdapterRegistry()
    item = _item("file.txt", "file.txt")
    assert registry.adapt_to_jd(item) is None


@pytest.mark.ci
def test_registry_adapt_to_jd_uses_registered_adapter(default_config, fs_adapter) -> None:
    """adapt_to_jd routes to FileSystemAdapter and returns JD number (line 347)."""
    from methodologies.johnny_decimal.adapters import AdapterRegistry

    registry = AdapterRegistry()
    registry.register(fs_adapter)
    item = _item("file.txt", "parent/file.txt")
    result = registry.adapt_to_jd(item)
    assert result is not None
    from methodologies.johnny_decimal.categories import NumberLevel

    assert result.level == NumberLevel.CATEGORY  # depth-2 path → CATEGORY level
    assert result.area is not None
    assert result.category is not None


@pytest.mark.ci
def test_registry_adapt_from_jd_para_path(para_config) -> None:
    """adapt_from_jd routes to PARAAdapter for methodology='para' (line 365)."""
    from methodologies.johnny_decimal.adapters import (
        AdapterRegistry,
        FileSystemAdapter,
        PARAAdapter,
    )
    from methodologies.johnny_decimal.categories import JohnnyDecimalNumber

    registry = AdapterRegistry()
    registry.register(PARAAdapter(para_config))
    registry.register(FileSystemAdapter(para_config))

    jd_num = JohnnyDecimalNumber(area=10, category=1)
    result = registry.adapt_from_jd(jd_num, "doc.pdf", methodology="para")
    assert result is not None
    assert result.name == "doc.pdf"


@pytest.mark.ci
def test_registry_adapt_from_jd_filesystem_path(default_config) -> None:
    """adapt_from_jd routes to FileSystemAdapter for methodology='filesystem' (line 366)."""
    from methodologies.johnny_decimal.adapters import AdapterRegistry, FileSystemAdapter
    from methodologies.johnny_decimal.categories import JohnnyDecimalNumber

    registry = AdapterRegistry()
    registry.register(FileSystemAdapter(default_config))

    jd_num = JohnnyDecimalNumber(area=10, category=1)
    result = registry.adapt_from_jd(jd_num, "doc.pdf", methodology="filesystem")
    assert result is not None
    assert isinstance(result.path, Path)
    assert result.name == "doc.pdf"


@pytest.mark.ci
def test_registry_adapt_from_jd_unknown_methodology_returns_none(
    default_config,
) -> None:
    """adapt_from_jd returns None when methodology doesn't match any adapter (line 369)."""
    from methodologies.johnny_decimal.adapters import AdapterRegistry, FileSystemAdapter
    from methodologies.johnny_decimal.categories import JohnnyDecimalNumber

    registry = AdapterRegistry()
    registry.register(FileSystemAdapter(default_config))

    jd_num = JohnnyDecimalNumber(area=10, category=1)
    result = registry.adapt_from_jd(jd_num, "doc.pdf", methodology="unknown")
    assert result is None
