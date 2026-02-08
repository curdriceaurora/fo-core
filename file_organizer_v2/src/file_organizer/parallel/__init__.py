"""
Parallel file processing system.

This module provides concurrent file processing capabilities using
concurrent.futures, with configurable thread/process pools, scheduling
strategies, progress reporting, and retry logic.
"""

from .config import ExecutorType, ParallelConfig
from .processor import ParallelProcessor
from .result import BatchResult, FileResult
from .scheduler import PriorityStrategy, TaskScheduler

__all__ = [
    "BatchResult",
    "ExecutorType",
    "FileResult",
    "ParallelConfig",
    "ParallelProcessor",
    "PriorityStrategy",
    "TaskScheduler",
]

__version__ = "1.0.0"
