"""Tests for build.yml CI/CD pipeline (CLI-only build pipeline)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "build.yml"

pytestmark = pytest.mark.ci


def _load_workflow() -> dict:
    """Load and parse build.yml as a YAML dict."""
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _build_step_names() -> list[str]:
    """Return the list of step names from the build job."""
    jobs = _load_workflow().get("jobs", {})
    return [s.get("name", "") for s in jobs.get("build", {}).get("steps", [])]


def _build_step_run_text() -> str:
    """Return all run-script text from build job steps concatenated."""
    jobs = _load_workflow().get("jobs", {})
    return " ".join(s.get("run", "") for s in jobs.get("build", {}).get("steps", []))


class TestWorkflowFile:
    def test_workflow_file_exists(self) -> None:
        """build.yml must exist."""
        assert WORKFLOW_PATH.exists()

    def test_workflow_is_valid_yaml(self) -> None:
        """build.yml must be valid YAML with a jobs key."""
        wf = _load_workflow()
        assert "jobs" in wf


class TestNoRustOrTauriSteps:
    def test_no_test_rust_job(self) -> None:
        """test-rust job must be absent — Rust/Tauri removed in #1114."""
        jobs = _load_workflow().get("jobs", {})
        assert "test-rust" not in jobs

    def test_no_rust_toolchain_step(self) -> None:
        """No step should reference a Rust toolchain action."""
        raw = WORKFLOW_PATH.read_text(encoding="utf-8")
        assert "dtolnay/rust-toolchain" not in raw
        assert "Swatinem/rust-cache" not in raw

    def test_no_cargo_commands(self) -> None:
        """No run: block should invoke cargo or rustc."""
        raw = WORKFLOW_PATH.read_text(encoding="utf-8")
        assert "cargo " not in raw
        assert "rustc " not in raw

    def test_no_tauri_steps(self) -> None:
        """No step should reference Tauri build or src-tauri paths."""
        raw_lower = WORKFLOW_PATH.read_text(encoding="utf-8").lower()
        assert ("tauri" not in raw_lower) and ("src-tauri" not in raw_lower)

    def test_no_tauri_signing_secrets(self) -> None:
        """TAURI_SIGNING_* env vars must not appear."""
        raw = WORKFLOW_PATH.read_text(encoding="utf-8")
        assert "TAURI_SIGNING" not in raw

    def test_no_combined_artifacts(self) -> None:
        """combined-artifacts directory must not be referenced."""
        raw = WORKFLOW_PATH.read_text(encoding="utf-8")
        assert "combined-artifacts" not in raw


class TestCLIBuildStep:
    def test_cli_build_step_present(self) -> None:
        """A step building the CLI executable must exist."""
        run_text = _build_step_run_text()
        assert "python scripts/build.py --clean" in run_text

    def test_no_desktop_runtime_dependencies(self) -> None:
        """CLI-only workflow should not install desktop GUI runtime deps."""
        run_text = _build_step_run_text()
        assert "pywebview" not in run_text
        assert "libwebkit2gtk" not in run_text
        assert "gir1.2-webkit2" not in run_text

    def test_linux_verification_avoids_appimage_and_checks_appimage_exists(self) -> None:
        """Linux verification should target CLI binary and separately assert AppImage output."""
        run_text = _build_step_run_text()
        assert '-not -name "*.AppImage"' in run_text
        assert "No AppImage found in dist/" in run_text


class TestArtifactUpload:
    def test_artifacts_from_dist(self) -> None:
        """Artifacts must be uploaded from dist/, not combined-artifacts/."""
        jobs = _load_workflow().get("jobs", {})
        steps = jobs.get("build", {}).get("steps", [])
        upload_steps = [s for s in steps if "upload-artifact" in s.get("uses", "")]
        assert upload_steps, "No upload-artifact step found"
        for step in upload_steps:
            path = step.get("with", {}).get("path", "")
            assert path.startswith("dist"), f"Upload path must start with 'dist', got: {path!r}"


class TestReleaseJob:
    def test_release_needs_build_only(self) -> None:
        """release job must depend only on 'build', not 'test-rust'."""
        jobs = _load_workflow().get("jobs", {})
        release = jobs.get("release", {})
        needs = release.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        assert needs == ["build"], f"release job must need only 'build', got: {needs}"

    def test_release_triggered_on_tags(self) -> None:
        """release job must only run on tag pushes."""
        jobs = _load_workflow().get("jobs", {})
        release = jobs.get("release", {})
        cond = release.get("if", "")
        assert "refs/tags/v" in cond, (
            f"release job must be guarded by tag condition, got if: {cond!r}"
        )
