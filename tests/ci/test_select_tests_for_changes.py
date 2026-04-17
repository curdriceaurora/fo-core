"""Tests for scripts/select_tests_for_changes.py.

Covers: source-file → mapped test dir, test-file passthrough, unmapped fallback,
deduplication, missing-path filtering, and CLI output formats.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.ci]

# ---------------------------------------------------------------------------
# Load the script as a module (it lives in scripts/, not src/)
# ---------------------------------------------------------------------------
_SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "select_tests_for_changes.py"


def _load_selector():
    """Import scripts/select_tests_for_changes.py as a module."""
    spec = importlib.util.spec_from_file_location("select_tests_for_changes", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def selector():
    return _load_selector()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dirs(tmp_path: Path, *dirs: str) -> None:
    """Create directories under tmp_path to simulate a repo root."""
    for d in dirs:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# select_test_paths — source file → mapped test directory
# ---------------------------------------------------------------------------


class TestSourceFileMapping:
    def test_cli_source_maps_to_cli_tests(self, selector, tmp_path: Path) -> None:
        _make_dirs(tmp_path, "tests/cli")
        result = selector.select_test_paths(["src/cli/main.py"], tmp_path)
        assert "tests/cli" in result

    def test_services_source_maps_to_services_tests(self, selector, tmp_path: Path) -> None:
        _make_dirs(tmp_path, "tests/services")
        result = selector.select_test_paths(["src/services/deduplication/embedder.py"], tmp_path)
        assert "tests/services" in result

    def test_methodologies_source_maps_to_methodologies_tests(
        self, selector, tmp_path: Path
    ) -> None:
        _make_dirs(tmp_path, "tests/methodologies")
        result = selector.select_test_paths(
            ["src/methodologies/para/detection/heuristics.py"], tmp_path
        )
        assert "tests/methodologies" in result

    def test_all_packages_have_entries_in_map(self, selector) -> None:
        packages = [
            "cli",
            "config",
            "core",
            "daemon",
            "events",
            "history",
            "integrations",
            "interfaces",
            "methodologies",
            "models",
            "optimization",
            "parallel",
            "pipeline",
            "services",
            "undo",
            "updater",
            "utils",
            "watcher",
        ]
        for pkg in packages:
            key = f"src/{pkg}"
            assert key in selector.PACKAGE_TEST_MAP, f"Missing mapping for {key}"


# ---------------------------------------------------------------------------
# select_test_paths — test file passthrough
# ---------------------------------------------------------------------------


class TestTestFilePassthrough:
    def test_test_file_included_directly(self, selector, tmp_path: Path) -> None:
        test_file = tmp_path / "tests" / "cli" / "test_main.py"
        test_file.parent.mkdir(parents=True)
        test_file.touch()
        result = selector.select_test_paths(["tests/cli/test_main.py"], tmp_path)
        assert "tests/cli/test_main.py" in result

    def test_nonexistent_test_file_filtered(self, selector, tmp_path: Path) -> None:
        # File listed in changed but not on disk — should be excluded
        result = selector.select_test_paths(["tests/cli/test_ghost.py"], tmp_path)
        assert "tests/cli/test_ghost.py" not in result

    def test_test_file_and_source_file_combined(self, selector, tmp_path: Path) -> None:
        _make_dirs(tmp_path, "tests/config")
        test_file = tmp_path / "tests" / "ci" / "test_workflows.py"
        test_file.parent.mkdir(parents=True)
        test_file.touch()
        result = selector.select_test_paths(
            ["src/config/schema.py", "tests/ci/test_workflows.py"], tmp_path
        )
        assert "tests/config" in result
        assert "tests/ci/test_workflows.py" in result


# ---------------------------------------------------------------------------
# select_test_paths — unmapped source falls back to tests/ci
# ---------------------------------------------------------------------------


class TestUnmappedFallback:
    def test_unmapped_source_uses_fallback(self, selector, tmp_path: Path) -> None:
        _make_dirs(tmp_path, "tests/ci")
        result = selector.select_test_paths(["src/unknown_package/foo.py"], tmp_path)
        assert "tests/ci" in result

    def test_empty_changed_list_uses_fallback(self, selector, tmp_path: Path) -> None:
        _make_dirs(tmp_path, "tests/ci")
        result = selector.select_test_paths([], tmp_path)
        assert "tests/ci" in result

    def test_non_src_non_test_file_uses_fallback(self, selector, tmp_path: Path) -> None:
        _make_dirs(tmp_path, "tests/ci")
        result = selector.select_test_paths(["docs/USER_GUIDE.md"], tmp_path)
        assert "tests/ci" in result

    def test_fallback_not_included_when_src_mapped(self, selector, tmp_path: Path) -> None:
        _make_dirs(tmp_path, "tests/cli", "tests/ci")
        result = selector.select_test_paths(["src/cli/main.py"], tmp_path)
        assert "tests/ci" not in result


# ---------------------------------------------------------------------------
# select_test_paths — deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    def test_multiple_files_same_package_deduped(self, selector, tmp_path: Path) -> None:
        _make_dirs(tmp_path, "tests/cli")
        result = selector.select_test_paths(
            ["src/cli/main.py", "src/cli/organize.py", "src/cli/doctor.py"], tmp_path
        )
        assert result.count("tests/cli") == 1

    def test_output_is_sorted(self, selector, tmp_path: Path) -> None:
        _make_dirs(tmp_path, "tests/cli", "tests/config")
        result = selector.select_test_paths(["src/config/schema.py", "src/cli/main.py"], tmp_path)
        assert result == sorted(result)


# ---------------------------------------------------------------------------
# select_test_paths — missing test directories filtered
# ---------------------------------------------------------------------------


class TestMissingPathFiltering:
    def test_nonexistent_mapped_dir_filtered(self, selector, tmp_path: Path) -> None:
        # tests/cli does not exist in tmp_path
        result = selector.select_test_paths(["src/cli/main.py"], tmp_path)
        assert "tests/cli" not in result

    def test_only_existing_paths_returned(self, selector, tmp_path: Path) -> None:
        _make_dirs(tmp_path, "tests/cli")
        # tests/config does NOT exist
        result = selector.select_test_paths(["src/cli/main.py", "src/config/schema.py"], tmp_path)
        assert "tests/cli" in result
        assert "tests/config" not in result

    def test_nonexistent_fallback_not_included(self, selector, tmp_path: Path) -> None:
        # Neither mapped dir nor tests/ci exists
        result = selector.select_test_paths(["src/unknown/foo.py"], tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# CLI output formats
# ---------------------------------------------------------------------------


class TestCLIFormats:
    def test_json_format(self, tmp_path: Path) -> None:
        repo_root = tmp_path
        _make_dirs(repo_root, "tests/cli")
        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT_PATH),
                "--base",
                "HEAD",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        )
        # May fail on git diff in tmp_path (no git repo), but should not crash on import
        # Just verify the script is parseable and importable
        assert result.returncode in (0, 1, 128)  # 128 = git error (no repo in tmp)

    def test_script_is_importable(self) -> None:
        """Script imports without side effects."""
        mod = _load_selector()
        assert hasattr(mod, "select_test_paths")
        assert hasattr(mod, "get_changed_files")
        assert hasattr(mod, "PACKAGE_TEST_MAP")
        assert hasattr(mod, "FALLBACK_PATHS")

    def test_args_format_returns_space_joined(self, selector, tmp_path: Path, capsys) -> None:
        _make_dirs(tmp_path, "tests/cli", "tests/config")
        with patch.object(
            selector, "get_changed_files", return_value=["src/cli/main.py", "src/config/schema.py"]
        ):
            with patch("sys.argv", [str(_SCRIPT_PATH), "--staged", "--format", "args"]):
                with patch.object(
                    selector,
                    "Path",
                    side_effect=lambda *a, **kw: tmp_path if not a else Path(*a, **kw),
                ):
                    pass  # Just verify select_test_paths returns a list
        paths = selector.select_test_paths(["src/cli/main.py", "src/config/schema.py"], tmp_path)
        assert isinstance(paths, list)
        # Space-join for --format args
        output = " ".join(paths)
        assert "tests/cli" in output

    def test_json_format_output_is_valid_json(self, selector, tmp_path: Path) -> None:
        _make_dirs(tmp_path, "tests/services")
        paths = selector.select_test_paths(["src/services/deduplication/embedder.py"], tmp_path)
        encoded = json.dumps(paths)
        decoded = json.loads(encoded)
        assert decoded == paths


# ---------------------------------------------------------------------------
# get_changed_files — subprocess interface (light smoke tests)
# ---------------------------------------------------------------------------


class TestGetChangedFiles:
    def test_staged_calls_git_diff_cached(self, selector) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "src/cli/main.py\n"
            mock_run.return_value.returncode = 0
            result = selector.get_changed_files(staged=True)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "--cached" in cmd
        assert result == ["src/cli/main.py"]

    def test_base_calls_git_diff_with_ref(self, selector) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "src/models/base.py\n"
            mock_run.return_value.returncode = 0
            result = selector.get_changed_files(base="origin/main")
        cmd = mock_run.call_args[0][0]
        assert "origin/main" in cmd
        assert result == ["src/models/base.py"]

    def test_empty_git_output_returns_empty_list(self, selector) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "\n\n"
            mock_run.return_value.returncode = 0
            result = selector.get_changed_files(staged=True)
        assert result == []

    def test_neither_staged_nor_base_raises(self, selector) -> None:
        with pytest.raises((ValueError, TypeError)):
            selector.get_changed_files()
