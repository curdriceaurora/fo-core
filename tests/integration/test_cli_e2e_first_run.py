"""End-to-end CLI tests for the first-run / setup flow.

Covers the exact crash site in v2.0.0-beta.4 (setup_callback with no
subcommand) plus the full setup → config-on-disk → gate chain.

TestSetupNoSubcommand    — tests the callback path that crashed in beta.4
TestSetupWritesConfigToDisk — real SetupWizard + ConfigManager: file lands on disk
TestFirstRunGate         — _check_setup_completed blocks/passes correctly
TestSetupValidation      — validate_config catches configuration errors
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
import yaml
from typer.testing import CliRunner

# Import the real gate function BEFORE the autouse _bypass_setup_wizard fixture
# patches cli.organize._check_setup_completed.  Importing at module level gives
# us a direct reference to the original function object; the autouse patch
# replaces the module-namespace attribute, leaving this alias untouched.
from cli.organize import _check_setup_completed as _real_check
from cli.setup import setup_app
from core.backend_detector import InstalledModel, OllamaStatus
from core.hardware_profile import GpuType, HardwareProfile

pytestmark = [pytest.mark.integration, pytest.mark.ci]

_runner = CliRunner()

_SMALL_MODEL = "qwen2.5:3b-instruct-q4_K_M"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _fake_hardware() -> HardwareProfile:
    """8 GB RAM, no GPU — recommended_text_model() returns _SMALL_MODEL."""
    return HardwareProfile(
        gpu_type=GpuType.NONE,
        gpu_name=None,
        vram_bytes=0,
        ram_bytes=8 * 1024**3,
        cpu_cores=4,
        os_name="Linux",
        arch="x86_64",
    )


def _fake_ollama_running(models_count: int = 1) -> OllamaStatus:
    return OllamaStatus(
        installed=True,
        running=True,
        version="0.3.0",
        models_count=models_count,
    )


def _fake_models(name: str = _SMALL_MODEL) -> list[InstalledModel]:
    return [InstalledModel(name=name)]


def _make_caps_mock(
    *,
    ollama_running: bool = True,
    ollama_installed: bool = True,
    models: list[Any] | None = None,
) -> MagicMock:
    """Return a SystemCapabilities mock suitable for CLI-level tests."""
    m = models or []
    caps = MagicMock()
    caps.hardware.gpu_type.value = "none"
    caps.hardware.gpu_name = None
    caps.hardware.vram_gb = 0.0
    caps.hardware.ram_gb = 8.0
    caps.hardware.cpu_cores = 4
    caps.hardware.os_name = "Linux"
    caps.hardware.recommended_text_model.return_value = _SMALL_MODEL
    caps.ollama_status.running = ollama_running
    caps.ollama_status.installed = ollama_installed
    caps.ollama_status.version = "0.3.0" if ollama_running else None
    caps.ollama_status.models_count = len(m)
    caps.installed_models = m
    return caps


def _make_cfg_mock(
    text_model: str = _SMALL_MODEL,
    methodology: str = "none",
) -> MagicMock:
    cfg = MagicMock()
    cfg.profile_name = "default"
    cfg.default_methodology = methodology
    cfg.models.text_model = text_model
    cfg.models.vision_model = None
    cfg.models.temperature = 0.5
    cfg.models.framework = "ollama"
    cfg.models.device = "cpu"
    return cfg


# ---------------------------------------------------------------------------
# Shared fixture: re-point DEFAULT_CONFIG_DIR at the isolated XDG env
# ---------------------------------------------------------------------------


@pytest.fixture()
def _live_config_dir(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Patch DEFAULT_CONFIG_DIR to the test's isolated XDG_CONFIG_HOME/fo.

    _isolate_user_env (autouse) sets XDG_CONFIG_HOME = tmp_path/xdg_config
    before each test.  config.manager.DEFAULT_CONFIG_DIR is a module-level
    constant evaluated at import time — before that override lands — so
    ConfigManager() would resolve to the real user config dir without this
    fixture.  Patching the constant at test runtime ensures both the CLI's
    SetupWizard and the gate's ConfigManager() agree on the same isolated
    directory.
    """
    from config.path_manager import get_config_dir

    live_dir = get_config_dir()  # reads XDG_CONFIG_HOME already set by _isolate_user_env
    monkeypatch.setattr("config.manager.DEFAULT_CONFIG_DIR", live_dir)
    live_dir.mkdir(parents=True, exist_ok=True)
    return live_dir


# ---------------------------------------------------------------------------
# TestSetupNoSubcommand
# ---------------------------------------------------------------------------


class TestSetupNoSubcommand:
    """setup_app invoked with no subcommand must not crash.

    In v2.0.0-beta.4 the setup_callback called ctx.invoke(setup_run) without
    explicit kwargs.  Click/Typer passed the raw typer.OptionInfo default
    objects as parameter values; setup_run then called mode.lower() on an
    OptionInfo, raising AttributeError.

    Fixed in PR #368: ctx.invoke(setup_run, mode="quick-start",
    profile="default", dry_run=False).
    """

    def test_no_subcommand_exits_zero(self) -> None:
        model = MagicMock()
        model.name = _SMALL_MODEL
        model.size = 2_000_000_000
        caps = _make_caps_mock(ollama_running=True, models=[model])
        cfg = _make_cfg_mock()

        with (
            patch("cli.setup.SetupWizard") as mock_wiz_cls,
            patch("cli.setup.console"),
            patch("cli.setup.confirm_action", return_value=True),
        ):
            mock_wiz = mock_wiz_cls.return_value
            mock_wiz.detect_capabilities.return_value = caps
            mock_wiz.generate_config.return_value = cfg
            mock_wiz.validate_config.return_value = (True, [])

            result = _runner.invoke(setup_app, [])

        assert result.exit_code == 0

    def test_no_subcommand_runs_full_quick_start_flow(self) -> None:
        model = MagicMock()
        model.name = _SMALL_MODEL
        model.size = 2_000_000_000
        caps = _make_caps_mock(ollama_running=True, models=[model])
        cfg = _make_cfg_mock()

        with (
            patch("cli.setup.SetupWizard") as mock_wiz_cls,
            patch("cli.setup.console"),
            patch("cli.setup.confirm_action", return_value=True),
        ):
            mock_wiz = mock_wiz_cls.return_value
            mock_wiz.detect_capabilities.return_value = caps
            mock_wiz.generate_config.return_value = cfg
            mock_wiz.validate_config.return_value = (True, [])

            result = _runner.invoke(setup_app, [])

        assert result.exit_code == 0
        mock_wiz.detect_capabilities.assert_called_once()
        mock_wiz.generate_config.assert_called_once()
        mock_wiz.validate_config.assert_called_once()
        mock_wiz.save_config.assert_called_once()

    def test_no_subcommand_uses_quick_start_wizard_mode(self) -> None:
        """Callback must pass WizardMode.QUICK_START — not POWER_USER."""
        from core.setup_wizard import WizardMode

        caps = _make_caps_mock(ollama_running=False, ollama_installed=False, models=[])
        cfg = _make_cfg_mock()

        with (
            patch("cli.setup.SetupWizard") as mock_wiz_cls,
            patch("cli.setup.console"),
            patch("cli.setup.confirm_action", return_value=True),
        ):
            mock_wiz = mock_wiz_cls.return_value
            mock_wiz.detect_capabilities.return_value = caps
            mock_wiz.generate_config.return_value = cfg
            mock_wiz.validate_config.return_value = (True, [])

            result = _runner.invoke(setup_app, [])

        assert result.exit_code == 0
        mock_wiz_cls.assert_called_once_with(mode=WizardMode.QUICK_START)


# ---------------------------------------------------------------------------
# TestSetupWritesConfigToDisk
# ---------------------------------------------------------------------------


class TestSetupWritesConfigToDisk:
    """Real SetupWizard + ConfigManager: config file must land on disk.

    Only hardware/Ollama detection is mocked; the wizard and config manager
    run as they would in production.
    """

    def test_quick_start_writes_setup_completed_true(self, _live_config_dir: Path) -> None:
        with (
            patch("core.setup_wizard.detect_hardware", return_value=_fake_hardware()),
            patch("core.setup_wizard.detect_ollama", return_value=_fake_ollama_running()),
            patch(
                "core.setup_wizard.list_installed_models",
                return_value=_fake_models(),
            ),
            patch("cli.setup.console"),
            patch("cli.setup.confirm_action", return_value=True),
        ):
            result = _runner.invoke(setup_app, ["run", "--mode", "quick-start"])

        assert result.exit_code == 0

        config_file = _live_config_dir / "config.yaml"
        assert config_file.exists(), (
            f"Config file not written to {config_file}; CLI output: {result.output!r}"
        )

        data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        profile = data["profiles"]["default"]
        assert profile["setup_completed"] is True
        assert profile["models"]["text_model"] == _SMALL_MODEL

    def test_no_subcommand_also_writes_setup_completed_true(self, _live_config_dir: Path) -> None:
        """The callback path (beta.4 crash site) must also reach save_config."""
        with (
            patch("core.setup_wizard.detect_hardware", return_value=_fake_hardware()),
            patch("core.setup_wizard.detect_ollama", return_value=_fake_ollama_running()),
            patch(
                "core.setup_wizard.list_installed_models",
                return_value=_fake_models(),
            ),
            patch("cli.setup.console"),
            patch("cli.setup.confirm_action", return_value=True),
        ):
            result = _runner.invoke(setup_app, [])  # no subcommand — the crash path

        assert result.exit_code == 0

        config_file = _live_config_dir / "config.yaml"
        assert config_file.exists()
        data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert data["profiles"]["default"]["setup_completed"] is True

    def test_dry_run_does_not_write_file(self, _live_config_dir: Path) -> None:
        with (
            patch("core.setup_wizard.detect_hardware", return_value=_fake_hardware()),
            patch("core.setup_wizard.detect_ollama", return_value=_fake_ollama_running()),
            patch(
                "core.setup_wizard.list_installed_models",
                return_value=_fake_models(),
            ),
            patch("cli.setup.console"),
            patch("cli.setup.confirm_action", return_value=True),
        ):
            result = _runner.invoke(setup_app, ["run", "--dry-run"])

        assert result.exit_code == 0
        assert not (_live_config_dir / "config.yaml").exists(), (
            "Dry run must not write config to disk"
        )

    def test_save_declined_does_not_write_file(self, _live_config_dir: Path) -> None:
        with (
            patch("core.setup_wizard.detect_hardware", return_value=_fake_hardware()),
            patch("core.setup_wizard.detect_ollama", return_value=_fake_ollama_running()),
            patch(
                "core.setup_wizard.list_installed_models",
                return_value=_fake_models(),
            ),
            patch("cli.setup.console"),
            patch("cli.setup.confirm_action", return_value=False),  # user declines
        ):
            result = _runner.invoke(setup_app, ["run", "--mode", "quick-start"])

        assert result.exit_code == 0
        assert not (_live_config_dir / "config.yaml").exists(), (
            "Declined save must not write config to disk"
        )


# ---------------------------------------------------------------------------
# TestFirstRunGate
# ---------------------------------------------------------------------------


class TestFirstRunGate:
    """_check_setup_completed: blocks when unconfigured, passes when done.

    _real_check is the original function (imported at module level before the
    autouse _bypass_setup_wizard patch replaces the module attribute), so
    these tests exercise the real gate logic.

    All tests use _live_config_dir so ConfigManager() resolves to the
    isolated test directory rather than the real ~/.config/fo.
    """

    def test_gate_blocks_when_no_config_file(self, _live_config_dir: Path) -> None:
        assert not (_live_config_dir / "config.yaml").exists()

        with pytest.raises(typer.Exit) as exc_info:
            _real_check()

        assert exc_info.value.exit_code == 1

    def test_gate_blocks_when_setup_completed_false(self, _live_config_dir: Path) -> None:
        (_live_config_dir / "config.yaml").write_text(
            "profiles:\n  default:\n    setup_completed: false\n    version: '1.0'\n",
            encoding="utf-8",
        )

        with pytest.raises(typer.Exit) as exc_info:
            _real_check()

        assert exc_info.value.exit_code == 1

    def test_gate_passes_when_setup_completed_true(self, _live_config_dir: Path) -> None:
        (_live_config_dir / "config.yaml").write_text(
            "profiles:\n"
            "  default:\n"
            "    setup_completed: true\n"
            "    version: '1.0'\n"
            f"    models:\n"
            f"      text_model: {_SMALL_MODEL}\n",
            encoding="utf-8",
        )

        result = _real_check()

        assert result is True

    def test_gate_passes_after_full_setup_run(self, _live_config_dir: Path) -> None:
        """Run setup end-to-end, then verify the real gate passes."""
        with (
            patch("core.setup_wizard.detect_hardware", return_value=_fake_hardware()),
            patch("core.setup_wizard.detect_ollama", return_value=_fake_ollama_running()),
            patch(
                "core.setup_wizard.list_installed_models",
                return_value=_fake_models(),
            ),
            patch("cli.setup.console"),
            patch("cli.setup.confirm_action", return_value=True),
        ):
            setup_result = _runner.invoke(setup_app, ["run", "--mode", "quick-start"])

        assert setup_result.exit_code == 0

        gate_result = _real_check()
        assert gate_result is True


# ---------------------------------------------------------------------------
# TestSetupValidation
# ---------------------------------------------------------------------------


class TestSetupValidation:
    """validate_config catches configuration errors before saving."""

    def test_validation_fails_when_ollama_running_but_no_models_installed(
        self,
    ) -> None:
        """Ollama running, no models → hardware-recommended model not in empty set."""
        ollama_no_models = OllamaStatus(
            installed=True, running=True, version="0.3.0", models_count=0
        )

        with (
            patch("core.setup_wizard.detect_hardware", return_value=_fake_hardware()),
            patch("core.setup_wizard.detect_ollama", return_value=ollama_no_models),
            patch("core.setup_wizard.list_installed_models", return_value=[]),
            patch("cli.setup.console"),
        ):
            result = _runner.invoke(setup_app, ["run", "--mode", "quick-start"])

        assert result.exit_code == 1

    def test_validation_fails_when_ollama_not_running(self) -> None:
        """Framework 'ollama' selected but Ollama service is not running → error."""
        ollama_stopped = OllamaStatus(installed=True, running=False)

        with (
            patch("core.setup_wizard.detect_hardware", return_value=_fake_hardware()),
            patch("core.setup_wizard.detect_ollama", return_value=ollama_stopped),
            patch("core.setup_wizard.list_installed_models", return_value=[]),
            patch("cli.setup.console"),
        ):
            result = _runner.invoke(setup_app, ["run", "--mode", "quick-start"])

        assert result.exit_code == 1

    def test_validation_passes_when_model_installed(self) -> None:
        """Ollama running with the recommended model installed → validation passes."""
        with (
            patch("core.setup_wizard.detect_hardware", return_value=_fake_hardware()),
            patch("core.setup_wizard.detect_ollama", return_value=_fake_ollama_running()),
            patch(
                "core.setup_wizard.list_installed_models",
                return_value=_fake_models(),
            ),
            patch("cli.setup.console"),
            patch("cli.setup.confirm_action", return_value=False),  # skip write
        ):
            result = _runner.invoke(setup_app, ["run", "--mode", "quick-start"])

        assert result.exit_code == 0
