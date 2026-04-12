"""Integration tests targeting uncovered branches in optimization modules.

Modules under test:
  - file_organizer.optimization.lazy_loader      (target: ≥80%)
  - file_organizer.optimization.buffer_pool      (target: ≥80%)
  - file_organizer.optimization.resource_monitor (target: ≥80%)
  - file_organizer.optimization.batch_sizer      (target: ≥80%)

All external I/O (psutil, subprocess, /proc files) is mocked so the tests
are hermetic and pass in any environment.
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock, mock_open, patch

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_base_model(name: str = "test-model") -> MagicMock:
    from file_organizer.models.base import BaseModel

    mock = MagicMock(spec=BaseModel)
    mock.config = MagicMock()
    mock.config.name = name
    return mock


def _make_model_config(
    name: str = "test-model",
    framework: str = "ollama",
    model_type: Any = None,
) -> Any:
    from file_organizer.models.base import ModelConfig, ModelType

    mt = model_type if model_type is not None else ModelType.TEXT
    return ModelConfig(name=name, model_type=mt, framework=framework)


# ===========================================================================
# LazyModelLoader — uncovered branches
# ===========================================================================


class TestLazyModelLoaderDefaultLoader:
    """Cover _default_loader's framework dispatch branches."""

    def test_default_loader_ollama_branch(self) -> None:
        """When framework='ollama' and no custom loader, TextModel path is taken."""
        from file_organizer.optimization.lazy_loader import LazyModelLoader

        config = _make_model_config(framework="ollama")
        fake_model = _make_base_model("ollama-model")

        with patch(
            "file_organizer.optimization.lazy_loader.LazyModelLoader._default_loader",
            return_value=fake_model,
        ) as mock_dl:
            lazy = LazyModelLoader(config)  # no custom loader
            result = lazy.model

        mock_dl.assert_called_once_with(config)
        assert result is fake_model
        assert lazy.is_loaded is True

    def test_default_loader_openai_branch(self) -> None:
        """framework='openai' routes through get_text_model + initialize."""
        from file_organizer.optimization.lazy_loader import LazyModelLoader

        config = _make_model_config(framework="openai")
        fake_model = _make_base_model("openai-model")

        with patch(
            "file_organizer.models.provider_factory.get_text_model",
            return_value=fake_model,
        ) as mock_gtm:
            result = LazyModelLoader._default_loader(config)

        mock_gtm.assert_called_once_with(config)
        fake_model.initialize.assert_called_once()
        assert result is fake_model

    def test_default_loader_unsupported_framework_raises(self) -> None:
        """An unknown framework raises ValueError from _default_loader."""
        from file_organizer.optimization.lazy_loader import LazyModelLoader

        config = _make_model_config(framework="unknown_framework_xyz")
        lazy = LazyModelLoader(config)  # no custom loader provided

        with pytest.raises(RuntimeError, match="Failed to load model"):
            _ = lazy.model

    def test_default_loader_unsupported_framework_inner_value_error(self) -> None:
        """_default_loader raises ValueError directly for unknown framework."""
        from file_organizer.optimization.lazy_loader import LazyModelLoader

        config = _make_model_config(framework="bogus")
        with pytest.raises(ValueError, match="Unsupported framework"):
            LazyModelLoader._default_loader(config)

    def test_default_loader_ollama_uses_text_model(self) -> None:
        """_default_loader 'ollama' branch instantiates TextModel and calls initialize."""
        from file_organizer.optimization.lazy_loader import LazyModelLoader

        config = _make_model_config(framework="ollama")
        fake_model = _make_base_model()

        # TextModel is imported inside the function body; patch at its source module.
        with patch(
            "file_organizer.models.text_model.TextModel",
            return_value=fake_model,
        ) as MockTextModel:
            result = LazyModelLoader._default_loader(config)

        MockTextModel.assert_called_once_with(config)
        fake_model.initialize.assert_called_once()
        assert result is fake_model

    def test_default_loader_llama_cpp_branch(self) -> None:
        """framework='llama_cpp' routes through get_text_model."""
        from file_organizer.optimization.lazy_loader import LazyModelLoader

        config = _make_model_config(framework="llama_cpp")
        fake_model = _make_base_model()

        with patch(
            "file_organizer.models.provider_factory.get_text_model",
            return_value=fake_model,
        ) as mock_gtm:
            result = LazyModelLoader._default_loader(config)

        mock_gtm.assert_called_once_with(config)
        fake_model.initialize.assert_called_once()
        assert result is fake_model

    def test_default_loader_claude_text_branch(self) -> None:
        """framework='claude' with TEXT model_type routes through get_text_model."""
        from file_organizer.models.base import ModelType
        from file_organizer.optimization.lazy_loader import LazyModelLoader

        config = _make_model_config(framework="claude", model_type=ModelType.TEXT)
        fake_model = _make_base_model()

        with patch(
            "file_organizer.models.provider_factory.get_text_model",
            return_value=fake_model,
        ) as mock_gtm:
            result = LazyModelLoader._default_loader(config)

        mock_gtm.assert_called_once_with(config)
        fake_model.initialize.assert_called_once()
        assert result is fake_model

    def test_default_loader_claude_vision_branch(self) -> None:
        """framework='claude' with VISION model_type routes through get_vision_model."""
        from file_organizer.models.base import ModelType
        from file_organizer.optimization.lazy_loader import LazyModelLoader

        config = _make_model_config(framework="claude", model_type=ModelType.VISION)
        fake_model = _make_base_model()

        with patch(
            "file_organizer.models.provider_factory.get_vision_model",
            return_value=fake_model,
        ) as mock_gvm:
            result = LazyModelLoader._default_loader(config)

        mock_gvm.assert_called_once_with(config)
        fake_model.initialize.assert_called_once()
        assert result is fake_model


class TestLazyModelLoaderEdgeCases:
    """Cover edge-case branches in LazyModelLoader."""

    def test_unload_when_cleanup_raises_does_not_propagate(self) -> None:
        """cleanup() raising should be swallowed and model should still be cleared."""
        from file_organizer.optimization.lazy_loader import LazyModelLoader

        model = _make_base_model()
        model.cleanup.side_effect = RuntimeError("cleanup boom")
        config = _make_model_config()
        lazy = LazyModelLoader(config, loader=lambda c: model)

        _ = lazy.model
        assert lazy.is_loaded is True

        # Should not raise even though cleanup() does
        lazy.unload()
        assert lazy.is_loaded is False

    def test_model_loaded_flag_stays_true_on_second_access(self) -> None:
        """Accessing .model a second time returns the cached instance (not reload)."""
        from file_organizer.optimization.lazy_loader import LazyModelLoader

        model = _make_base_model()
        call_count = [0]

        def counting_loader(c: Any) -> Any:
            call_count[0] += 1
            return model

        config = _make_model_config()
        lazy = LazyModelLoader(config, loader=counting_loader)

        first = lazy.model
        second = lazy.model

        assert first is second
        assert call_count[0] == 1

    def test_reload_after_unload_calls_loader_again(self) -> None:
        """After unload(), accessing .model triggers a fresh load."""
        from file_organizer.optimization.lazy_loader import LazyModelLoader

        call_count = [0]
        model = _make_base_model()

        def counting_loader(c: Any) -> Any:
            call_count[0] += 1
            return model

        config = _make_model_config()
        lazy = LazyModelLoader(config, loader=counting_loader)

        _ = lazy.model
        assert call_count[0] == 1

        lazy.unload()
        _ = lazy.model
        assert call_count[0] == 2

    def test_loader_failure_leaves_model_unloaded(self) -> None:
        """After a failed load, is_loaded must still be False."""
        from file_organizer.optimization.lazy_loader import LazyModelLoader

        config = _make_model_config()
        lazy = LazyModelLoader(
            config, loader=lambda c: (_ for _ in ()).throw(OSError("disk error"))
        )

        with pytest.raises(RuntimeError):
            _ = lazy.model

        assert lazy.is_loaded is False

    def test_concurrent_load_uses_double_checked_locking(self) -> None:
        """With the lock held the double-checked path returns already-loaded model."""
        from file_organizer.optimization.lazy_loader import LazyModelLoader

        model = _make_base_model()
        calls = [0]

        def loader(c: Any) -> Any:
            calls[0] += 1
            return model

        config = _make_model_config()
        lazy = LazyModelLoader(config, loader=loader)

        # Pre-load so _model is set
        _ = lazy.model

        # Now simulate contention: many threads calling .model simultaneously
        results = []

        def access() -> None:
            results.append(lazy.model)

        threads = [threading.Thread(target=access) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert calls[0] == 1  # loaded exactly once
        assert all(r is model for r in results)


# ===========================================================================
# BufferPool — uncovered branches
# ===========================================================================


class TestBufferPoolAcquireEdgeCases:
    """Cover branches in BufferPool.acquire() not reached by existing tests."""

    def test_acquire_size_zero_raises(self) -> None:
        from file_organizer.optimization.buffer_pool import BufferPool

        pool = BufferPool(buffer_size=64, initial_buffers=1)
        with pytest.raises(ValueError, match="size must be > 0"):
            pool.acquire(size=0)

    def test_acquire_negative_size_raises(self) -> None:
        from file_organizer.optimization.buffer_pool import BufferPool

        pool = BufferPool(buffer_size=64, initial_buffers=1)
        with pytest.raises(ValueError, match="size must be > 0"):
            pool.acquire(size=-10)

    def test_acquire_oversize_not_pooled_not_returned_to_pool(self) -> None:
        """Oversize buffer increments in_use but not total_buffers."""
        from file_organizer.optimization.buffer_pool import BufferPool

        pool = BufferPool(buffer_size=64, initial_buffers=2, max_buffers=4)
        initial_total = pool.total_buffers
        buf = pool.acquire(size=512)
        assert len(buf) == 512
        assert pool.total_buffers == initial_total
        assert pool.in_use_count == 1
        pool.release(buf)
        assert pool.in_use_count == 0
        assert pool.total_buffers == initial_total

    def test_acquire_negative_timeout_raises(self) -> None:
        """timeout < 0 on a fully-saturated pool raises ValueError."""
        from file_organizer.optimization.buffer_pool import BufferPool

        pool = BufferPool(buffer_size=64, initial_buffers=1, max_buffers=1)
        # Exhaust all capacity
        held = pool.acquire()
        try:
            with pytest.raises(ValueError, match="timeout must be >= 0"):
                pool.acquire(timeout=-1.0)
        finally:
            pool.release(held)

    def test_acquire_timeout_zero_raises_timeout_error(self) -> None:
        """timeout=0 on exhausted pool (at max) raises TimeoutError immediately."""
        from file_organizer.optimization.buffer_pool import BufferPool

        pool = BufferPool(buffer_size=64, initial_buffers=1, max_buffers=1)
        held = pool.acquire()
        try:
            with pytest.raises(TimeoutError, match="Timed out waiting"):
                pool.acquire(timeout=0.0)
        finally:
            pool.release(held)

    def test_acquire_waits_and_succeeds_after_release(self) -> None:
        """Thread blocked on acquire() gets the buffer once another thread releases."""
        from file_organizer.optimization.buffer_pool import BufferPool

        pool = BufferPool(buffer_size=64, initial_buffers=1, max_buffers=1)
        held = pool.acquire()

        acquired: list[bytearray] = []
        error: list[Exception] = []
        about_to_acquire = threading.Event()

        def waiter() -> None:
            try:
                about_to_acquire.set()
                acquired.append(pool.acquire(timeout=2.0))
            except Exception as exc:
                error.append(exc)

        t = threading.Thread(target=waiter)
        t.start()
        # Wait until the waiter has signalled it is about to call acquire()
        about_to_acquire.wait(timeout=5.0)
        pool.release(held)
        t.join(timeout=2.0)

        assert not error, f"Waiter thread raised: {error}"
        assert len(acquired) == 1
        assert isinstance(acquired[0], bytearray)
        pool.release(acquired[0])

    def test_acquire_at_max_grows_after_waiting(self) -> None:
        """When pool is at max and a buffer is released, the waiting acquire
        returns a newly-available (or grown) buffer via the condition path."""
        from file_organizer.optimization.buffer_pool import BufferPool

        pool = BufferPool(buffer_size=32, initial_buffers=2, max_buffers=2)
        buf_a = pool.acquire()
        buf_b = pool.acquire()

        acquired: list[bytearray] = []
        about_to_acquire = threading.Event()

        def waiter() -> None:
            about_to_acquire.set()
            acquired.append(pool.acquire(timeout=2.0))

        t = threading.Thread(target=waiter)
        t.start()
        about_to_acquire.wait(timeout=5.0)
        pool.release(buf_a)
        t.join(timeout=2.0)

        assert len(acquired) == 1
        pool.release(buf_b)
        pool.release(acquired[0])


class TestBufferPoolReleaseEdgeCases:
    """Cover branches in BufferPool.release()."""

    def test_release_unknown_buffer_raises(self) -> None:
        from file_organizer.optimization.buffer_pool import BufferPool

        pool = BufferPool(buffer_size=64, initial_buffers=2)
        with pytest.raises(ValueError, match="not owned by this pool"):
            pool.release(bytearray(64))

    def test_release_resized_pooled_buffer_drops_count(self) -> None:
        """Returning a pooled buffer whose length was changed drops it from pool."""
        from file_organizer.optimization.buffer_pool import BufferPool

        pool = BufferPool(buffer_size=64, initial_buffers=2, max_buffers=4)
        initial_total = pool.total_buffers
        buf = pool.acquire()
        buf.extend(b"x")  # change length so it no longer matches buffer_size

        pool.release(buf)

        # Pool dropped this buffer → total_buffers decremented
        assert pool.total_buffers == initial_total - 1
        assert pool.in_use_count == 0


class TestBufferPoolUtilization:
    """Cover utilization edge cases."""

    def test_utilization_range_with_buffers_in_use(self) -> None:
        from file_organizer.optimization.buffer_pool import BufferPool

        pool = BufferPool(buffer_size=64, initial_buffers=4, max_buffers=8)
        b1 = pool.acquire()
        b2 = pool.acquire()
        util = pool.utilization
        assert 0.0 < util <= 1.0
        pool.release(b1)
        pool.release(b2)

    def test_utilization_with_only_oversize_in_use(self) -> None:
        """Oversize buffers count as in_use_ids but not pooled_ids → utilization stays 0."""
        from file_organizer.optimization.buffer_pool import BufferPool

        pool = BufferPool(buffer_size=64, initial_buffers=2, max_buffers=4)
        buf = pool.acquire(size=512)  # oversized — not in _pooled_ids
        util = pool.utilization
        # Oversize buffer not in pooled_ids → intersection is empty → util == 0.0
        assert util == 0.0
        pool.release(buf)


class TestBufferPoolResizeEdgeCases:
    """Cover resize() clamping and shrink paths."""

    def test_resize_target_below_initial_clamped(self) -> None:
        """resize(target < initial_buffers) clamps to initial_buffers."""
        from file_organizer.optimization.buffer_pool import BufferPool

        pool = BufferPool(buffer_size=64, initial_buffers=4, max_buffers=8)
        result = pool.resize(1)  # below initial_buffers=4 → clamped to 4
        assert result == 4

    def test_resize_target_above_max_clamped(self) -> None:
        """resize(target > max_buffers) clamps to max_buffers."""
        from file_organizer.optimization.buffer_pool import BufferPool

        pool = BufferPool(buffer_size=64, initial_buffers=2, max_buffers=5)
        result = pool.resize(100)  # above max=5 → clamped to 5
        assert result == 5

    def test_resize_invalid_target_raises(self) -> None:
        from file_organizer.optimization.buffer_pool import BufferPool

        pool = BufferPool(buffer_size=64, initial_buffers=2, max_buffers=5)
        with pytest.raises(ValueError, match="target_total_buffers must be > 0"):
            pool.resize(0)

    def test_resize_shrink_with_in_use_stops_at_in_use_count(self) -> None:
        """Shrink never evicts in-use buffers; only removes available ones."""
        from file_organizer.optimization.buffer_pool import BufferPool

        pool = BufferPool(buffer_size=64, initial_buffers=4, max_buffers=8)
        held = [pool.acquire() for _ in range(3)]
        # 3 in use, 1 available; resize to 2 can only remove the 1 available
        result = pool.resize(2)
        assert result >= 3  # can't go below in-use count
        for b in held:
            pool.release(b)

    def test_shrink_to_baseline_returns_initial_size(self) -> None:
        from file_organizer.optimization.buffer_pool import BufferPool

        pool = BufferPool(buffer_size=64, initial_buffers=3, max_buffers=8)
        pool.resize(7)
        result = pool.shrink_to_baseline()
        assert result == 3

    def test_peak_in_use_grows_monotonically(self) -> None:
        from file_organizer.optimization.buffer_pool import BufferPool

        pool = BufferPool(buffer_size=64, initial_buffers=5)
        bufs = [pool.acquire() for _ in range(4)]
        peak_a = pool.peak_in_use
        extra = pool.acquire()
        peak_b = pool.peak_in_use
        assert peak_b >= peak_a
        assert peak_b == 5
        for b in bufs + [extra]:
            pool.release(b)


# ===========================================================================
# ResourceMonitor — uncovered branches
# ===========================================================================


class TestResourceMonitorGetSystemMemoryTotal:
    """Cover get_system_memory_total() psutil and fallback paths."""

    def test_get_system_memory_total_via_psutil(self) -> None:
        from file_organizer.optimization.resource_monitor import ResourceMonitor

        mock_vm = MagicMock()
        mock_vm.total = 32 * 1024 * 1024 * 1024  # 32 GB

        with patch("psutil.virtual_memory", return_value=mock_vm):
            monitor = ResourceMonitor()
            total = monitor.get_system_memory_total()

        assert total == 32 * 1024 * 1024 * 1024

    def test_get_system_memory_total_fallback_when_no_psutil(self) -> None:
        from file_organizer.optimization.resource_monitor import ResourceMonitor

        monitor = ResourceMonitor()
        _8gb = 8 * 1024 * 1024 * 1024
        with (
            patch("psutil.virtual_memory", side_effect=ImportError("psutil not installed")),
            patch.object(
                ResourceMonitor,
                "_get_total_memory_fallback",
                return_value=_8gb,
            ) as mock_fallback,
        ):
            total = monitor.get_system_memory_total()

        assert total == _8gb
        mock_fallback.assert_called_once_with()

    def test_get_total_memory_fallback_proc_meminfo(self) -> None:
        """_get_total_memory_fallback reads MemTotal from /proc/meminfo on Linux."""
        from file_organizer.optimization.resource_monitor import ResourceMonitor

        fake_proc = "MemTotal:       16000000 kB\nOtherLine: 0 kB\n"
        with patch("builtins.open", mock_open(read_data=fake_proc)):
            total = ResourceMonitor._get_total_memory_fallback()

        assert total == 16_000_000 * 1024

    def test_get_total_memory_fallback_sysctl_path(self) -> None:
        """_get_total_memory_fallback falls back to sysctl on macOS."""
        from file_organizer.optimization.resource_monitor import ResourceMonitor

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "17179869184\n"

        with (
            patch("builtins.open", side_effect=FileNotFoundError),
            patch("subprocess.run", return_value=mock_result),
        ):
            total = ResourceMonitor._get_total_memory_fallback()

        assert total == 17_179_869_184

    def test_get_total_memory_fallback_sysctl_failure_returns_zero(self) -> None:
        from file_organizer.optimization.resource_monitor import ResourceMonitor

        mock_result = MagicMock()
        mock_result.returncode = 1

        with (
            patch("builtins.open", side_effect=FileNotFoundError),
            patch("subprocess.run", return_value=mock_result),
        ):
            total = ResourceMonitor._get_total_memory_fallback()

        assert total == 0


class TestResourceMonitorFallbackMemory:
    """Cover _get_memory_fallback branches."""

    def test_get_memory_fallback_proc_path(self) -> None:
        """_get_memory_fallback reads VmRSS and VmSize from /proc/self/status."""
        from file_organizer.optimization.resource_monitor import MemoryInfo, ResourceMonitor

        fake_status = "Name:  test\nVmRSS:    51200 kB\nVmSize:  204800 kB\n"
        fake_meminfo = "MemTotal: 16000000 kB\n"

        def fake_open(path: str, *args: Any, **kwargs: Any):
            if "status" in path:
                return mock_open(read_data=fake_status)()
            if "meminfo" in path:
                return mock_open(read_data=fake_meminfo)()
            raise FileNotFoundError(path)

        with patch("builtins.open", side_effect=fake_open):
            mem = ResourceMonitor._get_memory_fallback()

        assert isinstance(mem, MemoryInfo)
        assert mem.rss == 51200 * 1024
        assert mem.vms == 204800 * 1024
        assert mem.percent > 0.0

    def test_get_memory_fallback_resource_module_darwin(self) -> None:
        """On macOS, _get_memory_fallback falls back to resource.getrusage."""
        import sys

        from file_organizer.optimization.resource_monitor import MemoryInfo, ResourceMonitor

        mock_usage = MagicMock()
        mock_usage.ru_maxrss = 104857600  # 100 MB in bytes (macOS reports bytes)

        with (
            patch("builtins.open", side_effect=FileNotFoundError),
            patch("resource.getrusage", return_value=mock_usage),
            patch.object(sys, "platform", "darwin"),
        ):
            mem = ResourceMonitor._get_memory_fallback()

        assert isinstance(mem, MemoryInfo)
        assert mem.rss == 104857600

    def test_get_memory_fallback_resource_module_linux(self) -> None:
        """On Linux, _get_memory_fallback falls back to resource.getrusage (kB)."""
        import sys

        from file_organizer.optimization.resource_monitor import MemoryInfo, ResourceMonitor

        mock_usage = MagicMock()
        mock_usage.ru_maxrss = 102400  # 100 MB in kB (Linux reports kB)

        with (
            patch("builtins.open", side_effect=FileNotFoundError),
            patch("resource.getrusage", return_value=mock_usage),
            patch.object(sys, "platform", "linux"),
        ):
            mem = ResourceMonitor._get_memory_fallback()

        assert isinstance(mem, MemoryInfo)
        assert mem.rss == 102400 * 1024

    def test_get_memory_fallback_all_paths_unavailable_returns_zeros(self) -> None:
        """When both /proc and resource module fail, returns MemoryInfo with zeros."""
        from file_organizer.optimization.resource_monitor import MemoryInfo, ResourceMonitor

        with (
            patch("builtins.open", side_effect=FileNotFoundError),
            patch("resource.getrusage", side_effect=ImportError),
        ):
            mem = ResourceMonitor._get_memory_fallback()

        assert isinstance(mem, MemoryInfo)
        assert mem.rss == 0


class TestResourceMonitorGpuEdgeCases:
    """Cover _get_nvidia_gpu_memory edge-case branches."""

    def test_get_gpu_memory_nonzero_returncode_returns_none(self) -> None:
        from file_organizer.optimization.resource_monitor import ResourceMonitor

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        monitor = ResourceMonitor()
        with patch(
            "file_organizer.optimization.resource_monitor.subprocess.run",
            return_value=mock_result,
        ):
            result = monitor.get_gpu_memory()

        assert result is None

    def test_get_gpu_memory_empty_output_returns_none(self) -> None:
        from file_organizer.optimization.resource_monitor import ResourceMonitor

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        monitor = ResourceMonitor()
        with patch(
            "file_organizer.optimization.resource_monitor.subprocess.run",
            return_value=mock_result,
        ):
            result = monitor.get_gpu_memory()

        assert result is None

    def test_get_gpu_memory_too_few_csv_columns_returns_none(self) -> None:
        """A CSV line with fewer than 4 columns is treated as invalid."""
        from file_organizer.optimization.resource_monitor import ResourceMonitor

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "GPU Name, 10240\n"  # only 2 columns

        monitor = ResourceMonitor()
        with patch(
            "file_organizer.optimization.resource_monitor.subprocess.run",
            return_value=mock_result,
        ):
            result = monitor.get_gpu_memory()

        assert result is None

    def test_get_gpu_memory_subprocess_error_returns_none(self) -> None:
        import subprocess

        from file_organizer.optimization.resource_monitor import ResourceMonitor

        monitor = ResourceMonitor()
        with patch(
            "file_organizer.optimization.resource_monitor.subprocess.run",
            side_effect=subprocess.SubprocessError("failed"),
        ):
            result = monitor.get_gpu_memory()

        assert result is None

    def test_get_gpu_memory_value_error_returns_none(self) -> None:
        """ValueError during parsing is caught and None is returned."""
        from file_organizer.optimization.resource_monitor import ResourceMonitor

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "GPU, not_a_number, 0, 0\n"

        monitor = ResourceMonitor()
        with patch(
            "file_organizer.optimization.resource_monitor.subprocess.run",
            return_value=mock_result,
        ):
            result = monitor.get_gpu_memory()

        assert result is None

    def test_should_evict_zero_threshold_always_evicts(self) -> None:
        """threshold_percent=0 means any memory usage triggers eviction."""
        from file_organizer.optimization.resource_monitor import MemoryInfo, ResourceMonitor

        monitor = ResourceMonitor()
        mem = MemoryInfo(rss=1, vms=1, percent=0.01)
        with patch.object(monitor, "get_memory_usage", return_value=mem):
            assert monitor.should_evict(threshold_percent=0.0) is True

    def test_should_evict_threshold_100_never_evicts(self) -> None:
        """threshold_percent=100 means memory is never considered too high."""
        from file_organizer.optimization.resource_monitor import MemoryInfo, ResourceMonitor

        monitor = ResourceMonitor()
        mem = MemoryInfo(rss=1, vms=1, percent=99.9)
        with patch.object(monitor, "get_memory_usage", return_value=mem):
            assert monitor.should_evict(threshold_percent=100.0) is False

    def test_get_gpu_memory_whitespace_only_output_returns_none(self) -> None:
        """stdout with only whitespace should return None."""
        from file_organizer.optimization.resource_monitor import ResourceMonitor

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "   \n  "

        monitor = ResourceMonitor()
        with patch(
            "file_organizer.optimization.resource_monitor.subprocess.run",
            return_value=mock_result,
        ):
            result = monitor.get_gpu_memory()

        assert result is None


# ===========================================================================
# AdaptiveBatchSizer — uncovered branches
# ===========================================================================


class TestAdaptiveBatchSizerMemoryPaths:
    """Cover _get_available_memory, _get_total_memory, _get_rss fallback paths."""

    def test_get_available_memory_proc_path(self) -> None:
        """_get_available_memory reads MemAvailable from /proc/meminfo."""
        from file_organizer.optimization.batch_sizer import AdaptiveBatchSizer

        fake_meminfo = "MemTotal: 16000000 kB\nMemAvailable:  8000000 kB\n"
        with patch("builtins.open", mock_open(read_data=fake_meminfo)):
            available = AdaptiveBatchSizer._get_available_memory()

        assert available == 8_000_000 * 1024

    def test_get_available_memory_uses_total_minus_rss_fallback(self) -> None:
        """Without /proc/meminfo, falls back to total - rss."""
        from file_organizer.optimization.batch_sizer import AdaptiveBatchSizer

        with (
            patch("builtins.open", side_effect=FileNotFoundError),
            patch.object(
                AdaptiveBatchSizer,
                "_get_total_memory",
                staticmethod(lambda: 8 * 1024 * 1024 * 1024),
            ),
            patch.object(
                AdaptiveBatchSizer,
                "_get_rss",
                staticmethod(lambda: 1 * 1024 * 1024 * 1024),
            ),
        ):
            available = AdaptiveBatchSizer._get_available_memory()

        assert available == 7 * 1024 * 1024 * 1024

    def test_get_available_memory_returns_zero_when_no_total(self) -> None:
        from file_organizer.optimization.batch_sizer import AdaptiveBatchSizer

        with (
            patch("builtins.open", side_effect=FileNotFoundError),
            patch.object(
                AdaptiveBatchSizer,
                "_get_total_memory",
                staticmethod(lambda: 0),
            ),
        ):
            available = AdaptiveBatchSizer._get_available_memory()

        assert available == 0

    def test_get_total_memory_proc_path(self) -> None:
        """_get_total_memory reads MemTotal from /proc/meminfo on Linux."""
        from file_organizer.optimization.batch_sizer import AdaptiveBatchSizer

        fake = "MemTotal: 32000000 kB\n"
        with patch("builtins.open", mock_open(read_data=fake)):
            total = AdaptiveBatchSizer._get_total_memory()

        assert total == 32_000_000 * 1024

    def test_get_total_memory_sysctl_path(self) -> None:
        """_get_total_memory falls back to sysctl hw.memsize on macOS."""
        from file_organizer.optimization.batch_sizer import AdaptiveBatchSizer

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "34359738368\n"

        with (
            patch("builtins.open", side_effect=FileNotFoundError),
            patch("subprocess.run", return_value=mock_result),
        ):
            total = AdaptiveBatchSizer._get_total_memory()

        assert total == 34_359_738_368

    def test_get_total_memory_returns_zero_when_all_paths_fail(self) -> None:
        from file_organizer.optimization.batch_sizer import AdaptiveBatchSizer

        mock_result = MagicMock()
        mock_result.returncode = 1

        with (
            patch("builtins.open", side_effect=FileNotFoundError),
            patch("subprocess.run", return_value=mock_result),
        ):
            total = AdaptiveBatchSizer._get_total_memory()

        assert total == 0

    def test_get_rss_proc_path(self) -> None:
        """_get_rss reads VmRSS from /proc/self/status on Linux."""
        from file_organizer.optimization.batch_sizer import AdaptiveBatchSizer

        fake_status = "Name: test\nVmRSS:    20480 kB\nOther: 0\n"
        with patch("builtins.open", mock_open(read_data=fake_status)):
            rss = AdaptiveBatchSizer._get_rss()

        assert rss == 20_480 * 1024

    def test_get_rss_resource_module_darwin(self) -> None:
        """_get_rss uses resource.getrusage on macOS (bytes)."""
        import sys

        from file_organizer.optimization.batch_sizer import AdaptiveBatchSizer

        mock_usage = MagicMock()
        mock_usage.ru_maxrss = 52428800  # 50 MB in bytes

        with (
            patch("builtins.open", side_effect=FileNotFoundError),
            patch("resource.getrusage", return_value=mock_usage),
            patch.object(sys, "platform", "darwin"),
        ):
            rss = AdaptiveBatchSizer._get_rss()

        assert rss == 52428800

    def test_get_rss_resource_module_linux(self) -> None:
        """_get_rss uses resource.getrusage on Linux (kB → bytes)."""
        import sys

        from file_organizer.optimization.batch_sizer import AdaptiveBatchSizer

        mock_usage = MagicMock()
        mock_usage.ru_maxrss = 51200  # 50 MB in kB

        with (
            patch("builtins.open", side_effect=FileNotFoundError),
            patch("resource.getrusage", return_value=mock_usage),
            patch.object(sys, "platform", "linux"),
        ):
            rss = AdaptiveBatchSizer._get_rss()

        assert rss == 51200 * 1024

    def test_get_rss_returns_zero_when_all_fail(self) -> None:
        from file_organizer.optimization.batch_sizer import AdaptiveBatchSizer

        with (
            patch("builtins.open", side_effect=FileNotFoundError),
            patch("resource.getrusage", side_effect=ImportError),
        ):
            rss = AdaptiveBatchSizer._get_rss()

        assert rss == 0


class TestAdaptiveBatchSizerEdgeCases:
    """Cover remaining edge-case branches in AdaptiveBatchSizer."""

    def test_adjust_from_feedback_actual_per_file_zero_returns_same_size(self) -> None:
        """When actual_memory=0 (per_file_cost=0), return the input batch_size unchanged."""
        from file_organizer.optimization.batch_sizer import AdaptiveBatchSizer

        sizer = AdaptiveBatchSizer()
        available = 100 * 1024 * 1024
        with patch.object(
            type(sizer),
            "_get_available_memory",
            staticmethod(lambda: available),
        ):
            result = sizer.adjust_from_feedback(actual_memory=0, batch_size=7)

        assert result == 7

    def test_calculate_batch_size_per_file_cost_zero_capped_at_file_count(self) -> None:
        """When all file sizes are 0 and overhead=0, batch_size = min(file_count, max)."""
        from file_organizer.optimization.batch_sizer import AdaptiveBatchSizer

        sizer = AdaptiveBatchSizer(target_memory_percent=70.0)
        sizer.set_bounds(min_size=1, max_size=100)
        available = 100 * 1024 * 1024
        with patch.object(
            type(sizer),
            "_get_available_memory",
            staticmethod(lambda: available),
        ):
            batch = sizer.calculate_batch_size([0] * 5, overhead_per_file=0)

        # 5 files < max 100 → batch_size = 5
        assert batch == 5

    def test_calculate_batch_size_fewer_files_than_formula(self) -> None:
        """batch_size is capped at len(file_sizes) when formula gives more."""
        from file_organizer.optimization.batch_sizer import AdaptiveBatchSizer

        sizer = AdaptiveBatchSizer(target_memory_percent=70.0)
        available = 10 * 1024 * 1024 * 1024  # 10 GB — formula yields large number
        with patch.object(
            type(sizer),
            "_get_available_memory",
            staticmethod(lambda: available),
        ):
            # 3 small files; formula would say thousands — capped at 3
            batch = sizer.calculate_batch_size([1024, 1024, 1024])

        assert batch == 3

    def test_adjust_from_feedback_available_zero_returns_min(self) -> None:
        from file_organizer.optimization.batch_sizer import AdaptiveBatchSizer

        sizer = AdaptiveBatchSizer()
        with patch.object(
            type(sizer),
            "_get_available_memory",
            staticmethod(lambda: 0),
        ):
            result = sizer.adjust_from_feedback(actual_memory=1024 * 1024, batch_size=5)

        assert result == sizer.min_batch_size

    def test_calculate_batch_size_with_overhead(self) -> None:
        """overhead_per_file is added to avg_file_size in the cost formula."""
        from file_organizer.optimization.batch_sizer import AdaptiveBatchSizer

        sizer = AdaptiveBatchSizer(target_memory_percent=50.0)
        available = 100 * 1024 * 1024  # 100 MB
        # Files: 5 MB each; overhead: 5 MB → per_file_cost = 10 MB
        # budget = 50 MB → batch_size = 5
        file_sizes = [5 * 1024 * 1024] * 20
        with patch.object(
            type(sizer),
            "_get_available_memory",
            staticmethod(lambda: available),
        ):
            batch = sizer.calculate_batch_size(file_sizes, overhead_per_file=5 * 1024 * 1024)

        assert batch == 5

    def test_target_memory_percent_100_is_valid(self) -> None:
        """target_memory_percent=100.0 is the upper valid boundary."""
        from file_organizer.optimization.batch_sizer import AdaptiveBatchSizer

        sizer = AdaptiveBatchSizer(target_memory_percent=100.0)
        assert sizer.target_memory_percent == 100.0

    def test_get_available_memory_returns_nonnegative_when_rss_exceeds_total(self) -> None:
        """If rss > total (edge case), available is clamped to 0."""
        from file_organizer.optimization.batch_sizer import AdaptiveBatchSizer

        with (
            patch("builtins.open", side_effect=FileNotFoundError),
            patch.object(
                AdaptiveBatchSizer,
                "_get_total_memory",
                staticmethod(lambda: 1 * 1024 * 1024),
            ),
            patch.object(
                AdaptiveBatchSizer,
                "_get_rss",
                staticmethod(lambda: 2 * 1024 * 1024),
            ),
        ):
            available = AdaptiveBatchSizer._get_available_memory()

        assert available == 0
