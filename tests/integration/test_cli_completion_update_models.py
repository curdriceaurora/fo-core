"""Integration tests for cli/completion.py, cli/update.py, cli/models_cli.py,
and cli/__init__.py.

Coverage targets:
- completion.py  → ≥ 80%
- update.py      → ≥ 80%
- models_cli.py  → ≥ 80%
- cli/__init__.py → ≥ 80%
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


def _make_release(version: str = "2.1.0", body: str = "Fix bugs.") -> MagicMock:
    """Return a MagicMock that looks like a ReleaseInfo."""
    release = MagicMock()
    release.version = version
    release.html_url = f"https://github.com/test/repo/releases/tag/v{version}"
    release.body = body
    return release


def _make_update_status(
    available: bool = False,
    current_version: str = "1.0.0",
    latest_version: str = "",
    release: object = None,
    install_result: object = None,
) -> MagicMock:
    """Return a MagicMock that looks like an UpdateStatus."""
    status = MagicMock()
    status.available = available
    status.current_version = current_version
    status.latest_version = latest_version
    status.release = release
    status.install_result = install_result
    return status


def _make_install_result(success: bool, message: str, sha256: str = "") -> MagicMock:
    result = MagicMock()
    result.success = success
    result.message = message
    result.sha256 = sha256
    return result


# ---------------------------------------------------------------------------
# Tests: cli/completion.py
# ---------------------------------------------------------------------------


class TestCompleteDirectory:
    """Tests for complete_directory()."""

    def test_yields_directories_matching_prefix(self, tmp_path: Path) -> None:
        from cli.completion import complete_directory

        (tmp_path / "alpha").mkdir()
        (tmp_path / "beta").mkdir()
        (tmp_path / "file.txt").write_text("x")

        prefix = str(tmp_path / "al")
        results = list(complete_directory(prefix))

        assert len(results) == 1
        path_str, kind = results[0]
        assert "alpha" in path_str
        assert kind == "directory"

    def test_yields_all_children_when_prefix_is_directory(self, tmp_path: Path) -> None:
        from cli.completion import complete_directory

        (tmp_path / "sub1").mkdir()
        (tmp_path / "sub2").mkdir()

        results = list(complete_directory(str(tmp_path)))
        names = [r[0] for r in results]
        assert any("sub1" in n for n in names)
        assert any("sub2" in n for n in names)
        assert all(k == "directory" for _, k in results)

    def test_returns_empty_when_oserror(self, tmp_path: Path) -> None:
        from cli.completion import complete_directory

        # Path that doesn't exist triggers OSError on iterdir
        results = list(complete_directory(str(tmp_path / "nonexistent" / "prefix")))
        assert results == []

    def test_empty_string_uses_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from cli.completion import complete_directory

        monkeypatch.chdir(tmp_path)
        (tmp_path / "mydir").mkdir()
        results = list(complete_directory(""))
        names = [r[0] for r in results]
        assert any("mydir" in n for n in names)

    def test_skips_files(self, tmp_path: Path) -> None:
        from cli.completion import complete_directory

        # Use a dedicated subdirectory that only has files (no subdirs)
        files_only = tmp_path / "files_only"
        files_only.mkdir()
        (files_only / "only_file.txt").write_text("data")
        results = list(complete_directory(str(files_only)))
        assert results == []


class TestCompleteFile:
    """Tests for complete_file()."""

    def test_yields_files_and_dirs_matching_prefix(self, tmp_path: Path) -> None:
        from cli.completion import complete_file

        (tmp_path / "report.txt").write_text("data")
        (tmp_path / "readme.md").write_text("doc")
        (tmp_path / "subdir").mkdir()

        prefix = str(tmp_path / "re")
        results = list(complete_file(prefix))
        names = [r[0] for r in results]
        assert any("report.txt" in n for n in names)
        assert any("readme.md" in n for n in names)

    def test_kind_for_directory_is_directory(self, tmp_path: Path) -> None:
        from cli.completion import complete_file

        (tmp_path / "subdir").mkdir()
        results = list(complete_file(str(tmp_path)))
        kinds = {r[0]: r[1] for r in results}
        assert any("subdir" in k for k in kinds)
        dir_entry = next(k for k in kinds if "subdir" in k)
        assert kinds[dir_entry] == "directory"

    def test_kind_for_file_is_extension(self, tmp_path: Path) -> None:
        from cli.completion import complete_file

        (tmp_path / "data.csv").write_text("a,b")
        results = list(complete_file(str(tmp_path)))
        kinds = {r[0]: r[1] for r in results}
        csv_entry = next(k for k in kinds if "data.csv" in k)
        assert kinds[csv_entry] == ".csv"

    def test_kind_for_extensionless_file_is_file(self, tmp_path: Path) -> None:
        from cli.completion import complete_file

        (tmp_path / "Makefile").write_text("all:")
        results = list(complete_file(str(tmp_path)))
        kinds = {r[0]: r[1] for r in results}
        makefile_entry = next(k for k in kinds if "Makefile" in k)
        assert kinds[makefile_entry] == "file"

    def test_returns_empty_on_oserror(self, tmp_path: Path) -> None:
        from cli.completion import complete_file

        results = list(complete_file(str(tmp_path / "no_such_dir" / "prefix")))
        assert results == []

    def test_empty_string_lists_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from cli.completion import complete_file

        monkeypatch.chdir(tmp_path)
        (tmp_path / "notes.txt").write_text("x")
        results = list(complete_file(""))
        names = [r[0] for r in results]
        assert any("notes.txt" in n for n in names)


# ---------------------------------------------------------------------------
# Tests: cli/update.py  (via CLI runner)
# ---------------------------------------------------------------------------


class TestUpdateCheckCommand:
    """Tests for `fo update check`."""

    def test_up_to_date(self, cli_runner: object) -> None:
        from cli.main import app

        status = _make_update_status(available=False, current_version="1.5.0")
        with patch("updater.UpdateManager") as MockMgr:
            MockMgr.return_value.check.return_value = status
            result = cli_runner.invoke(app, ["update", "check"])

        assert result.exit_code == 0
        assert "1.5.0" in result.output
        assert "up to date" in result.output.lower()

    def test_update_available_with_body(self, cli_runner: object) -> None:
        from cli.main import app

        release = _make_release(version="2.0.0", body="New features added.")
        status = _make_update_status(
            available=True,
            current_version="1.5.0",
            latest_version="2.0.0",
            release=release,
        )
        with patch("updater.UpdateManager") as MockMgr:
            MockMgr.return_value.check.return_value = status
            result = cli_runner.invoke(app, ["update", "check"])

        assert result.exit_code == 0
        assert "2.0.0" in result.output
        assert "New features" in result.output

    def test_update_available_no_body(self, cli_runner: object) -> None:
        from cli.main import app

        release = _make_release(version="2.0.0", body="")
        status = _make_update_status(
            available=True,
            current_version="1.5.0",
            latest_version="2.0.0",
            release=release,
        )
        with patch("updater.UpdateManager") as MockMgr:
            MockMgr.return_value.check.return_value = status
            result = cli_runner.invoke(app, ["update", "check"])

        assert result.exit_code == 0
        assert "2.0.0" in result.output
        # Should not crash when release body is empty
        assert "update" in result.output.lower()

    def test_custom_repo_option(self, cli_runner: object) -> None:
        from cli.main import app

        status = _make_update_status(available=False, current_version="1.0.0")
        with patch("updater.UpdateManager") as MockMgr:
            MockMgr.return_value.check.return_value = status
            result = cli_runner.invoke(app, ["update", "check", "--repo", "myorg/myrepo"])

        assert result.exit_code == 0
        # Verify UpdateManager was called with the custom repo
        MockMgr.assert_called_once_with(repo="myorg/myrepo", include_prereleases=False)

    def test_pre_flag(self, cli_runner: object) -> None:
        from cli.main import app

        status = _make_update_status(available=False, current_version="1.0.0")
        with patch("updater.UpdateManager") as MockMgr:
            MockMgr.return_value.check.return_value = status
            result = cli_runner.invoke(app, ["update", "check", "--pre"])

        assert result.exit_code == 0
        MockMgr.assert_called_once_with(repo="curdriceaurora/fo-core", include_prereleases=True)


class TestUpdateInstallCommand:
    """Tests for `fo update install`."""

    def test_already_up_to_date(self, cli_runner: object) -> None:
        from cli.main import app

        status = _make_update_status(available=False, current_version="2.0.0")
        with patch("updater.UpdateManager") as MockMgr:
            MockMgr.return_value.update.return_value = status
            result = cli_runner.invoke(app, ["update", "install"])

        assert result.exit_code == 0
        assert "up to date" in result.output.lower()

    def test_update_check_failed(self, cli_runner: object) -> None:
        from cli.main import app

        status = _make_update_status(available=True, install_result=None)
        with patch("updater.UpdateManager") as MockMgr:
            MockMgr.return_value.update.return_value = status
            result = cli_runner.invoke(app, ["update", "install"])

        assert result.exit_code == 1

    def test_install_success_with_sha256(self, cli_runner: object) -> None:
        from cli.main import app

        install_result = _make_install_result(
            success=True,
            message="Updated successfully.",
            sha256="abc123def456" * 4,
        )
        status = _make_update_status(
            available=True,
            current_version="1.0.0",
            install_result=install_result,
        )
        with patch("updater.UpdateManager") as MockMgr:
            MockMgr.return_value.update.return_value = status
            result = cli_runner.invoke(app, ["update", "install"])

        assert result.exit_code == 0
        assert "Updated successfully" in result.output
        assert "SHA256" in result.output

    def test_install_success_without_sha256(self, cli_runner: object) -> None:
        from cli.main import app

        install_result = _make_install_result(success=True, message="Installed.", sha256="")
        status = _make_update_status(available=True, install_result=install_result)
        with patch("updater.UpdateManager") as MockMgr:
            MockMgr.return_value.update.return_value = status
            result = cli_runner.invoke(app, ["update", "install"])

        assert result.exit_code == 0
        assert "Installed." in result.output

    def test_install_failure(self, cli_runner: object) -> None:
        from cli.main import app

        install_result = _make_install_result(success=False, message="Checksum mismatch.")
        status = _make_update_status(available=True, install_result=install_result)
        with patch("updater.UpdateManager") as MockMgr:
            MockMgr.return_value.update.return_value = status
            result = cli_runner.invoke(app, ["update", "install"])

        assert result.exit_code == 1
        assert "Checksum mismatch" in result.output

    def test_dry_run_flag(self, cli_runner: object) -> None:
        from cli.main import app

        install_result = _make_install_result(success=True, message="Dry run complete.")
        status = _make_update_status(available=True, install_result=install_result)
        with patch("updater.UpdateManager") as MockMgr:
            MockMgr.return_value.update.return_value = status
            result = cli_runner.invoke(app, ["update", "install", "--dry-run"])

        assert result.exit_code == 0
        MockMgr.return_value.update.assert_called_once_with(dry_run=True)


class TestUpdateRollbackCommand:
    """Tests for `fo update rollback`."""

    def test_rollback_success(self, cli_runner: object) -> None:
        from cli.main import app

        with patch("updater.UpdateInstaller") as MockInstaller:
            MockInstaller.return_value.rollback.return_value = True
            result = cli_runner.invoke(app, ["update", "rollback"])

        assert result.exit_code == 0
        assert "Rolled back" in result.output

    def test_rollback_no_backup(self, cli_runner: object) -> None:
        from cli.main import app

        with patch("updater.UpdateInstaller") as MockInstaller:
            MockInstaller.return_value.rollback.return_value = False
            result = cli_runner.invoke(app, ["update", "rollback"])

        assert result.exit_code == 1
        assert "No backup" in result.output


# ---------------------------------------------------------------------------
# Tests: cli/models_cli.py  (via CLI runner)
# ---------------------------------------------------------------------------


class TestModelListCommand:
    """Tests for `fo model list`."""

    def test_list_no_filter(self, cli_runner: object) -> None:
        from cli.main import app

        with patch("models.model_manager.ModelManager") as MockMgr:
            MockMgr.return_value.display_models.return_value = None
            result = cli_runner.invoke(app, ["model", "list"])

        assert result.exit_code == 0
        MockMgr.return_value.display_models.assert_called_once_with(type_filter=None)

    def test_list_with_type_filter(self, cli_runner: object) -> None:
        from cli.main import app

        with patch("models.model_manager.ModelManager") as MockMgr:
            MockMgr.return_value.display_models.return_value = None
            result = cli_runner.invoke(app, ["model", "list", "--type", "text"])

        assert result.exit_code == 0
        MockMgr.return_value.display_models.assert_called_once_with(type_filter="text")


class TestModelPullCommand:
    """Tests for `fo model pull`."""

    def test_pull_success(self, cli_runner: object) -> None:
        from cli.main import app

        with patch("models.model_manager.ModelManager") as MockMgr:
            MockMgr.return_value.pull_model.return_value = True
            result = cli_runner.invoke(app, ["model", "pull", "llama3:8b"])

        assert result.exit_code == 0
        MockMgr.return_value.pull_model.assert_called_once_with(name="llama3:8b")

    def test_pull_failure_exits_1(self, cli_runner: object) -> None:
        from cli.main import app

        with patch("models.model_manager.ModelManager") as MockMgr:
            MockMgr.return_value.pull_model.return_value = False
            result = cli_runner.invoke(app, ["model", "pull", "bad-model"])

        assert result.exit_code == 1


class TestModelCacheCommand:
    """Tests for `fo model cache`."""

    def test_cache_with_data(self, cli_runner: object, tmp_path) -> None:
        from cli.main import app

        with patch("models.model_manager.ModelManager") as MockMgr:
            MockMgr.return_value.cache_info.return_value = {
                "models_cached": 3,
                "cache_dir": str(tmp_path / "models"),
            }
            result = cli_runner.invoke(app, ["model", "cache"])

        assert result.exit_code == 0
        assert "models_cached" in result.output
        assert "3" in result.output

    def test_cache_no_data(self, cli_runner: object) -> None:
        from cli.main import app

        with patch("models.model_manager.ModelManager") as MockMgr:
            MockMgr.return_value.cache_info.return_value = {}
            result = cli_runner.invoke(app, ["model", "cache"])

        assert result.exit_code == 0
        assert "No cache data" in result.output

    def test_cache_none_response(self, cli_runner: object) -> None:
        from cli.main import app

        with patch("models.model_manager.ModelManager") as MockMgr:
            MockMgr.return_value.cache_info.return_value = None
            result = cli_runner.invoke(app, ["model", "cache"])

        assert result.exit_code == 0
        assert "No cache data" in result.output


# ---------------------------------------------------------------------------
# Tests: cli/__init__.py  (lazy import machinery)
# ---------------------------------------------------------------------------


class TestCliInitLazyImports:
    """Tests for the __getattr__ lazy-import mechanism in cli/__init__.py."""

    def test_app_attribute_resolves(self) -> None:
        import cli as cli_pkg
        from cli.main import app as real_app

        app = cli_pkg.app
        assert app is real_app

    def test_complete_directory_resolves(self) -> None:
        import cli as cli_pkg
        from cli.completion import complete_directory as real_fn

        fn = cli_pkg.complete_directory
        assert fn is real_fn

    def test_complete_file_resolves(self) -> None:
        import cli as cli_pkg
        from cli.completion import complete_file as real_fn

        fn = cli_pkg.complete_file
        assert fn is real_fn

    def test_update_app_resolves(self) -> None:
        import cli as cli_pkg
        from cli.update import update_app as real_app

        app = cli_pkg.update_app
        assert app is real_app

    def test_copilot_app_resolves(self) -> None:
        import cli as cli_pkg
        from cli.copilot import copilot_app as real_app

        app = cli_pkg.copilot_app
        assert app is real_app

    def test_unknown_attribute_raises_attribute_error(self) -> None:
        import cli as cli_pkg

        with pytest.raises(AttributeError, match="has no attribute"):
            _ = cli_pkg.this_does_not_exist_at_all  # type: ignore[attr-defined]

    def test_cached_after_first_access(self) -> None:
        """Accessing the same attribute twice returns the same object, and the
        name is stored in the module's __dict__ after first access."""
        import cli as cli_pkg

        # Ensure it's not already cached (may have been loaded by other tests)
        cli_pkg.__dict__.pop("complete_directory", None)

        first = cli_pkg.complete_directory
        # After first access the value must be cached in module dict
        assert "complete_directory" in cli_pkg.__dict__
        second = cli_pkg.complete_directory
        assert first is second

    def test_all_exports_listed_in_all(self) -> None:
        import cli as cli_pkg

        assert "app" in cli_pkg.__all__
        assert "update_app" in cli_pkg.__all__
        assert "complete_directory" in cli_pkg.__all__
