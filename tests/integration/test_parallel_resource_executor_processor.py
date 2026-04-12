"""Integration tests for parallel infrastructure modules.

Covers:
  - parallel/resource_manager.py  — ResourceManager, ResourceConfig, ResourceType
  - parallel/executor.py          — create_executor (extended coverage)
  - parallel/processor.py         — ParallelProcessor (extended coverage)
  - parallel/throttle.py          — RateThrottler (extended coverage)
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# ResourceConfig
# ---------------------------------------------------------------------------


class TestResourceConfig:
    def test_default_values(self) -> None:
        from file_organizer.parallel.resource_manager import ResourceConfig

        cfg = ResourceConfig()
        assert cfg.max_cpu_percent == 80.0
        assert cfg.max_memory_mb == 1024
        assert cfg.max_io_operations == 10
        assert cfg.max_gpu_percent == 0.0

    def test_custom_values(self) -> None:
        from file_organizer.parallel.resource_manager import ResourceConfig

        cfg = ResourceConfig(
            max_cpu_percent=50.0,
            max_memory_mb=512,
            max_io_operations=5,
            max_gpu_percent=25.0,
        )
        assert cfg.max_cpu_percent == 50.0
        assert cfg.max_memory_mb == 512
        assert cfg.max_io_operations == 5
        assert cfg.max_gpu_percent == 25.0

    def test_zero_cpu_raises(self) -> None:
        from file_organizer.parallel.resource_manager import ResourceConfig

        with pytest.raises(ValueError, match="max_cpu_percent must be > 0"):
            ResourceConfig(max_cpu_percent=0)

    def test_negative_cpu_raises(self) -> None:
        from file_organizer.parallel.resource_manager import ResourceConfig

        with pytest.raises(ValueError, match="max_cpu_percent must be > 0"):
            ResourceConfig(max_cpu_percent=-10.0)

    def test_zero_memory_raises(self) -> None:
        from file_organizer.parallel.resource_manager import ResourceConfig

        with pytest.raises(ValueError, match="max_memory_mb must be > 0"):
            ResourceConfig(max_memory_mb=0)

    def test_negative_memory_raises(self) -> None:
        from file_organizer.parallel.resource_manager import ResourceConfig

        with pytest.raises(ValueError, match="max_memory_mb must be > 0"):
            ResourceConfig(max_memory_mb=-1)

    def test_zero_io_operations_raises(self) -> None:
        from file_organizer.parallel.resource_manager import ResourceConfig

        with pytest.raises(ValueError, match="max_io_operations must be > 0"):
            ResourceConfig(max_io_operations=0)

    def test_negative_io_operations_raises(self) -> None:
        from file_organizer.parallel.resource_manager import ResourceConfig

        with pytest.raises(ValueError, match="max_io_operations must be > 0"):
            ResourceConfig(max_io_operations=-1)

    def test_negative_gpu_raises(self) -> None:
        from file_organizer.parallel.resource_manager import ResourceConfig

        with pytest.raises(ValueError, match="max_gpu_percent must be >= 0"):
            ResourceConfig(max_gpu_percent=-5.0)

    def test_zero_gpu_is_valid(self) -> None:
        from file_organizer.parallel.resource_manager import ResourceConfig

        cfg = ResourceConfig(max_gpu_percent=0.0)
        assert cfg.max_gpu_percent == 0.0


# ---------------------------------------------------------------------------
# ResourceManager
# ---------------------------------------------------------------------------


class TestResourceManagerInit:
    def test_config_property(self) -> None:
        from file_organizer.parallel.resource_manager import ResourceConfig, ResourceManager

        cfg = ResourceConfig(max_cpu_percent=60.0)
        mgr = ResourceManager(cfg)
        assert mgr.config is cfg

    def test_initial_used_is_zero(self) -> None:
        from file_organizer.parallel.resource_manager import (
            ResourceConfig,
            ResourceManager,
            ResourceType,
        )

        mgr = ResourceManager(ResourceConfig())
        for rt in ResourceType:
            assert mgr.get_used(rt) == 0.0

    def test_initial_available_equals_limit(self) -> None:
        from file_organizer.parallel.resource_manager import (
            ResourceConfig,
            ResourceManager,
            ResourceType,
        )

        cfg = ResourceConfig(
            max_cpu_percent=80.0,
            max_memory_mb=512,
            max_io_operations=8,
            max_gpu_percent=0.0,
        )
        mgr = ResourceManager(cfg)
        assert mgr.get_available(ResourceType.CPU) == 80.0
        assert mgr.get_available(ResourceType.MEMORY) == 512.0
        assert mgr.get_available(ResourceType.IO) == 8.0


class TestResourceManagerAcquire:
    def test_acquire_within_limit_returns_true(self) -> None:
        from file_organizer.parallel.resource_manager import (
            ResourceConfig,
            ResourceManager,
            ResourceType,
        )

        mgr = ResourceManager(ResourceConfig(max_cpu_percent=80.0))
        assert mgr.acquire(ResourceType.CPU, 40.0) is True

    def test_acquire_exactly_at_limit_returns_true(self) -> None:
        from file_organizer.parallel.resource_manager import (
            ResourceConfig,
            ResourceManager,
            ResourceType,
        )

        mgr = ResourceManager(ResourceConfig(max_cpu_percent=80.0))
        assert mgr.acquire(ResourceType.CPU, 80.0) is True

    def test_acquire_exceeding_limit_returns_false(self) -> None:
        from file_organizer.parallel.resource_manager import (
            ResourceConfig,
            ResourceManager,
            ResourceType,
        )

        mgr = ResourceManager(ResourceConfig(max_cpu_percent=80.0))
        assert mgr.acquire(ResourceType.CPU, 81.0) is False

    def test_acquire_reduces_available(self) -> None:
        from file_organizer.parallel.resource_manager import (
            ResourceConfig,
            ResourceManager,
            ResourceType,
        )

        mgr = ResourceManager(ResourceConfig(max_cpu_percent=100.0))
        mgr.acquire(ResourceType.CPU, 30.0)
        assert mgr.get_available(ResourceType.CPU) == 70.0

    def test_acquire_increases_used(self) -> None:
        from file_organizer.parallel.resource_manager import (
            ResourceConfig,
            ResourceManager,
            ResourceType,
        )

        mgr = ResourceManager(ResourceConfig(max_memory_mb=1024))
        mgr.acquire(ResourceType.MEMORY, 256.0)
        assert mgr.get_used(ResourceType.MEMORY) == 256.0

    def test_acquire_zero_always_succeeds(self) -> None:
        from file_organizer.parallel.resource_manager import (
            ResourceConfig,
            ResourceManager,
            ResourceType,
        )

        mgr = ResourceManager(ResourceConfig())
        assert mgr.acquire(ResourceType.CPU, 0.0) is True

    def test_acquire_negative_raises(self) -> None:
        from file_organizer.parallel.resource_manager import (
            ResourceConfig,
            ResourceManager,
            ResourceType,
        )

        mgr = ResourceManager(ResourceConfig())
        with pytest.raises(ValueError, match="amount must be >= 0"):
            mgr.acquire(ResourceType.CPU, -1.0)

    def test_acquire_unknown_resource_raises(self) -> None:
        from file_organizer.parallel.resource_manager import ResourceConfig, ResourceManager

        mgr = ResourceManager(ResourceConfig())
        with pytest.raises(ValueError, match="Unknown resource type"):
            mgr.acquire("nonexistent", 10.0)

    def test_sequential_acquires_respect_limit(self) -> None:
        from file_organizer.parallel.resource_manager import (
            ResourceConfig,
            ResourceManager,
            ResourceType,
        )

        mgr = ResourceManager(ResourceConfig(max_io_operations=3))
        assert mgr.acquire(ResourceType.IO, 1.0) is True
        assert mgr.acquire(ResourceType.IO, 1.0) is True
        assert mgr.acquire(ResourceType.IO, 1.0) is True
        assert mgr.acquire(ResourceType.IO, 1.0) is False


class TestResourceManagerRelease:
    def test_release_restores_available(self) -> None:
        from file_organizer.parallel.resource_manager import (
            ResourceConfig,
            ResourceManager,
            ResourceType,
        )

        mgr = ResourceManager(ResourceConfig(max_cpu_percent=100.0))
        mgr.acquire(ResourceType.CPU, 50.0)
        mgr.release(ResourceType.CPU, 50.0)
        assert mgr.get_available(ResourceType.CPU) == 100.0

    def test_release_clamps_to_zero(self) -> None:
        from file_organizer.parallel.resource_manager import (
            ResourceConfig,
            ResourceManager,
            ResourceType,
        )

        mgr = ResourceManager(ResourceConfig(max_cpu_percent=100.0))
        mgr.release(ResourceType.CPU, 999.0)  # release more than acquired
        assert mgr.get_used(ResourceType.CPU) == 0.0

    def test_release_negative_raises(self) -> None:
        from file_organizer.parallel.resource_manager import (
            ResourceConfig,
            ResourceManager,
            ResourceType,
        )

        mgr = ResourceManager(ResourceConfig())
        with pytest.raises(ValueError, match="amount must be >= 0"):
            mgr.release(ResourceType.CPU, -1.0)

    def test_release_unknown_resource_raises(self) -> None:
        from file_organizer.parallel.resource_manager import ResourceConfig, ResourceManager

        mgr = ResourceManager(ResourceConfig())
        with pytest.raises(ValueError, match="Unknown resource type"):
            mgr.release("unknown_type", 10.0)


class TestResourceManagerUtilization:
    def test_utilization_zero_when_idle(self) -> None:
        from file_organizer.parallel.resource_manager import (
            ResourceConfig,
            ResourceManager,
            ResourceType,
        )

        mgr = ResourceManager(ResourceConfig())
        assert mgr.get_utilization(ResourceType.CPU) == 0.0

    def test_utilization_one_when_fully_used(self) -> None:
        from file_organizer.parallel.resource_manager import (
            ResourceConfig,
            ResourceManager,
            ResourceType,
        )

        mgr = ResourceManager(ResourceConfig(max_cpu_percent=100.0))
        mgr.acquire(ResourceType.CPU, 100.0)
        assert mgr.get_utilization(ResourceType.CPU) == pytest.approx(1.0)

    def test_utilization_half_when_half_used(self) -> None:
        from file_organizer.parallel.resource_manager import (
            ResourceConfig,
            ResourceManager,
            ResourceType,
        )

        mgr = ResourceManager(ResourceConfig(max_cpu_percent=100.0))
        mgr.acquire(ResourceType.CPU, 50.0)
        assert mgr.get_utilization(ResourceType.CPU) == pytest.approx(0.5)

    def test_utilization_zero_gpu_limit_zero(self) -> None:
        from file_organizer.parallel.resource_manager import (
            ResourceConfig,
            ResourceManager,
            ResourceType,
        )

        mgr = ResourceManager(ResourceConfig(max_gpu_percent=0.0))
        assert mgr.get_utilization(ResourceType.GPU) == 0.0

    def test_utilization_unknown_resource_raises(self) -> None:
        from file_organizer.parallel.resource_manager import ResourceConfig, ResourceManager

        mgr = ResourceManager(ResourceConfig())
        with pytest.raises(ValueError, match="Unknown resource type"):
            mgr.get_utilization("nope")

    def test_get_available_unknown_resource_raises(self) -> None:
        from file_organizer.parallel.resource_manager import ResourceConfig, ResourceManager

        mgr = ResourceManager(ResourceConfig())
        with pytest.raises(ValueError, match="Unknown resource type"):
            mgr.get_available("ghost")

    def test_get_used_unknown_resource_raises(self) -> None:
        from file_organizer.parallel.resource_manager import ResourceConfig, ResourceManager

        mgr = ResourceManager(ResourceConfig())
        with pytest.raises(ValueError, match="Unknown resource type"):
            mgr.get_used("ghost")


class TestResourceManagerReset:
    def test_reset_clears_all_used(self) -> None:
        from file_organizer.parallel.resource_manager import (
            ResourceConfig,
            ResourceManager,
            ResourceType,
        )

        mgr = ResourceManager(ResourceConfig(max_cpu_percent=100.0, max_memory_mb=512))
        mgr.acquire(ResourceType.CPU, 50.0)
        mgr.acquire(ResourceType.MEMORY, 256.0)
        mgr.reset()
        assert mgr.get_used(ResourceType.CPU) == 0.0
        assert mgr.get_used(ResourceType.MEMORY) == 0.0

    def test_reset_restores_full_availability(self) -> None:
        from file_organizer.parallel.resource_manager import (
            ResourceConfig,
            ResourceManager,
            ResourceType,
        )

        cfg = ResourceConfig(max_io_operations=5)
        mgr = ResourceManager(cfg)
        mgr.acquire(ResourceType.IO, 5.0)
        mgr.reset()
        assert mgr.get_available(ResourceType.IO) == 5.0


class TestResourceManagerThreadSafety:
    def test_concurrent_acquires_do_not_exceed_limit(self) -> None:
        from file_organizer.parallel.resource_manager import (
            ResourceConfig,
            ResourceManager,
            ResourceType,
        )

        mgr = ResourceManager(ResourceConfig(max_cpu_percent=10.0))
        results: list[bool] = []
        lock = threading.Lock()

        def try_acquire() -> None:
            result = mgr.acquire(ResourceType.CPU, 1.0)
            with lock:
                results.append(result)

        threads = [threading.Thread(target=try_acquire) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # At most 10 should succeed (limit is 10.0, each acquires 1.0)
        successful = sum(1 for r in results if r)
        assert successful <= 10


# ---------------------------------------------------------------------------
# create_executor — extended coverage
# ---------------------------------------------------------------------------


class TestCreateExecutorExtended:
    def test_process_executor_fallback_on_os_error(self) -> None:
        from file_organizer.parallel.executor import create_executor

        with patch(
            "file_organizer.parallel.executor.ProcessPoolExecutor",
            side_effect=OSError("semaphore limit"),
        ):
            executor, executor_type = create_executor("process", max_workers=2)
            try:
                assert executor_type == "thread"
                assert isinstance(executor, ThreadPoolExecutor)
            finally:
                executor.shutdown(wait=False)

    def test_process_executor_fallback_on_runtime_error(self) -> None:
        from file_organizer.parallel.executor import create_executor

        with patch(
            "file_organizer.parallel.executor.ProcessPoolExecutor",
            side_effect=RuntimeError("no semaphores"),
        ):
            executor, executor_type = create_executor("process", max_workers=1)
            try:
                assert executor_type == "thread"
            finally:
                executor.shutdown(wait=False)

    def test_thread_executor_submits_and_returns_result(self) -> None:
        from file_organizer.parallel.executor import create_executor

        executor, executor_type = create_executor("thread", max_workers=2)
        try:
            future = executor.submit(lambda: "hello")
            assert future.result(timeout=5) == "hello"
            assert executor_type == "thread"
        finally:
            executor.shutdown(wait=True)

    def test_multiple_tasks_run_concurrently(self) -> None:
        from file_organizer.parallel.executor import create_executor

        executor, _ = create_executor("thread", max_workers=4)
        try:
            futures = [
                executor.submit(
                    lambda i=i: i * 2,
                )
                for i in range(5)
            ]
            results = [f.result(timeout=5) for f in futures]
            assert sorted(results) == [0, 2, 4, 6, 8]
        finally:
            executor.shutdown(wait=True)


# ---------------------------------------------------------------------------
# ParallelProcessor — extended coverage
# ---------------------------------------------------------------------------


class TestParallelProcessorExtended:
    def test_process_batch_iter_empty_yields_nothing(self) -> None:
        from file_organizer.parallel.config import ParallelConfig
        from file_organizer.parallel.processor import ParallelProcessor

        cfg = ParallelConfig(max_workers=1, retry_count=0, timeout_per_file=30.0)
        proc = ParallelProcessor(cfg)
        results = list(proc.process_batch_iter([], lambda p: p.name))
        assert results == []

    def test_process_batch_iter_yields_results(self, tmp_path: Path) -> None:
        from file_organizer.parallel.config import ParallelConfig
        from file_organizer.parallel.processor import ParallelProcessor

        files = []
        for i in range(3):
            f = tmp_path / f"iter_{i}.txt"
            f.write_text("data", encoding="utf-8")
            files.append(f)

        cfg = ParallelConfig(max_workers=2, retry_count=0, timeout_per_file=30.0)
        proc = ParallelProcessor(cfg)
        results = list(proc.process_batch_iter(files, lambda p: p.stem))

        assert len(results) == 3
        assert all(r.success for r in results)

    def test_process_batch_iter_with_external_executor(self, tmp_path: Path) -> None:
        from file_organizer.parallel.config import ParallelConfig
        from file_organizer.parallel.processor import ParallelProcessor

        files = [tmp_path / f"e{i}.txt" for i in range(2)]
        for f in files:
            f.write_text("x", encoding="utf-8")

        cfg = ParallelConfig(max_workers=2, retry_count=0, timeout_per_file=30.0)
        proc = ParallelProcessor(cfg)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(proc.process_batch_iter(files, lambda p: "ok", executor=executor))

        assert len(results) == 2
        assert all(r.success for r in results)

    def test_process_batch_iter_failure_included(self, tmp_path: Path) -> None:
        from file_organizer.parallel.config import ParallelConfig
        from file_organizer.parallel.processor import ParallelProcessor

        f = tmp_path / "fail.txt"
        f.write_text("x", encoding="utf-8")

        cfg = ParallelConfig(max_workers=1, retry_count=0, timeout_per_file=30.0)
        proc = ParallelProcessor(cfg)

        def fail_fn(p: Path) -> None:
            raise ValueError("deliberate error")

        results = list(proc.process_batch_iter([f], fail_fn))
        assert len(results) == 1
        assert results[0].success is False
        assert "deliberate error" in results[0].error  # type: ignore[operator]

    def test_retry_retries_failed_files(self, tmp_path: Path) -> None:
        from file_organizer.parallel.config import ParallelConfig
        from file_organizer.parallel.processor import ParallelProcessor

        f = tmp_path / "retry.txt"
        f.write_text("content", encoding="utf-8")

        call_count = {"n": 0}

        def flaky(p: Path) -> str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("first attempt fails")
            return "ok"

        cfg = ParallelConfig(max_workers=1, retry_count=1, timeout_per_file=30.0)
        proc = ParallelProcessor(cfg)
        result = proc.process_batch(files=[f], process_fn=flaky)

        assert result.total == 1
        assert result.succeeded == 1
        assert call_count["n"] == 2

    def test_shutdown_is_noop(self) -> None:
        from file_organizer.parallel.config import ParallelConfig
        from file_organizer.parallel.processor import ParallelProcessor

        proc = ParallelProcessor(ParallelConfig())
        proc.shutdown()  # should not raise

    def test_config_property_returns_same_object(self) -> None:
        from file_organizer.parallel.config import ParallelConfig
        from file_organizer.parallel.processor import ParallelProcessor

        cfg = ParallelConfig(max_workers=2)
        proc = ParallelProcessor(cfg)
        assert proc.config is cfg

    def test_default_config_used_when_none(self) -> None:
        from file_organizer.parallel.config import ParallelConfig
        from file_organizer.parallel.processor import ParallelProcessor

        proc = ParallelProcessor(None)
        assert isinstance(proc.config, ParallelConfig)
        assert proc.config.max_workers is None  # None means use os.cpu_count()
        assert proc.config.retry_count == 2  # default retry count

    def test_process_batch_files_per_second_positive(self, tmp_path: Path) -> None:
        from file_organizer.parallel.config import ParallelConfig
        from file_organizer.parallel.processor import ParallelProcessor

        files = []
        for i in range(4):
            f = tmp_path / f"fps{i}.txt"
            f.write_text("data", encoding="utf-8")
            files.append(f)

        cfg = ParallelConfig(max_workers=2, retry_count=0, timeout_per_file=30.0)
        proc = ParallelProcessor(cfg)
        result = proc.process_batch(files, lambda p: "ok")

        assert result.files_per_second > 0

    def test_process_executor_type_uses_create_executor(self, tmp_path: Path) -> None:
        from file_organizer.parallel.config import ExecutorType, ParallelConfig
        from file_organizer.parallel.processor import ParallelProcessor

        f = tmp_path / "proc.txt"
        f.write_text("data", encoding="utf-8")

        cfg = ParallelConfig(
            max_workers=1,
            executor_type=ExecutorType.PROCESS,
            retry_count=0,
            timeout_per_file=30.0,
        )
        proc = ParallelProcessor(cfg)
        # Force thread fallback so lambda is picklable; verifies the process executor
        # config path is exercised and falls back gracefully.
        with patch(
            "file_organizer.parallel.executor.ProcessPoolExecutor",
            side_effect=OSError("no semaphores in test"),
        ):
            result = proc.process_batch([f], lambda p: "ok")
        assert result.succeeded == 1


# ---------------------------------------------------------------------------
# RateThrottler — extended coverage
# ---------------------------------------------------------------------------


class TestRateThrottlerExtended:
    def test_reset_clears_stats(self) -> None:
        from file_organizer.parallel.throttle import RateThrottler

        throttler = RateThrottler(max_rate=10.0)
        throttler.acquire()
        throttler.acquire()
        throttler.reset()
        stats = throttler.stats()
        assert stats.allowed == 0
        assert stats.denied == 0

    def test_reset_restores_full_bucket(self) -> None:
        from file_organizer.parallel.throttle import RateThrottler

        throttler = RateThrottler(max_rate=2.0)
        throttler.acquire()
        throttler.acquire()
        # Bucket now empty
        assert throttler.acquire() is False
        throttler.reset()
        # After reset, bucket is full again
        assert throttler.acquire() is True

    def test_stats_denied_count(self) -> None:
        from file_organizer.parallel.throttle import RateThrottler

        throttler = RateThrottler(max_rate=1.0)
        throttler.acquire()  # allowed
        throttler.acquire()  # denied
        stats = throttler.stats()
        assert stats.denied >= 1

    def test_stats_window_seconds_matches_config(self) -> None:
        from file_organizer.parallel.throttle import RateThrottler

        throttler = RateThrottler(max_rate=5.0, window_seconds=2.0)
        stats = throttler.stats()
        assert stats.window_seconds == 2.0

    def test_stats_max_rate_matches_config(self) -> None:
        from file_organizer.parallel.throttle import RateThrottler

        throttler = RateThrottler(max_rate=7.5)
        stats = throttler.stats()
        assert stats.max_rate == 7.5

    def test_stats_current_rate_zero_before_any_acquire(self) -> None:
        from file_organizer.parallel.throttle import RateThrottler

        throttler = RateThrottler(max_rate=10.0)
        stats = throttler.stats()
        assert stats.current_rate == 0.0

    def test_wait_acquires_token_eventually(self) -> None:
        from file_organizer.parallel.throttle import RateThrottler

        # High rate so wait returns almost immediately
        throttler = RateThrottler(max_rate=1000.0, window_seconds=1.0)
        # This should not hang
        throttler.wait()
        stats = throttler.stats()
        assert stats.allowed >= 1

    def test_concurrent_acquires_are_thread_safe(self) -> None:
        from file_organizer.parallel.throttle import RateThrottler

        throttler = RateThrottler(max_rate=100.0)
        successes: list[bool] = []
        lock = threading.Lock()

        def acquire_token() -> None:
            result = throttler.acquire()
            with lock:
                successes.append(result)

        threads = [threading.Thread(target=acquire_token) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # With max_rate=100.0 and 50 acquires, all should succeed
        assert all(successes)

    def test_negative_max_rate_raises(self) -> None:
        from file_organizer.parallel.throttle import RateThrottler

        with pytest.raises(ValueError, match="max_rate must be > 0"):
            RateThrottler(max_rate=-1.0)

    def test_negative_window_raises(self) -> None:
        from file_organizer.parallel.throttle import RateThrottler

        with pytest.raises(ValueError, match="window_seconds must be > 0"):
            RateThrottler(max_rate=1.0, window_seconds=-1.0)

    def test_token_refill_over_time(self) -> None:
        from file_organizer.parallel.throttle import RateThrottler

        # Rate of 100 tokens / 0.1s = 1000 tokens/s refill rate
        throttler = RateThrottler(max_rate=100.0, window_seconds=0.1)
        # Drain the bucket
        for _ in range(100):
            throttler.acquire()
        # Bucket is empty now
        assert throttler.acquire() is False
        # Manually advance the last_refill time to simulate elapsed time
        throttler._last_refill = throttler._last_refill - 0.2
        # Should have refilled
        assert throttler.acquire() is True

    def test_stats_allowed_increments_on_each_acquire(self) -> None:
        from file_organizer.parallel.throttle import RateThrottler

        throttler = RateThrottler(max_rate=10.0)
        for _ in range(5):
            throttler.acquire()
        stats = throttler.stats()
        assert stats.allowed == 5
