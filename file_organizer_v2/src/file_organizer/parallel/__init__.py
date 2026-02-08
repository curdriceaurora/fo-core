"""
Parallel file processing system.

This module provides concurrent file processing capabilities using
concurrent.futures, with configurable thread/process pools, scheduling
strategies, progress reporting, retry logic, and resumable batch
processing with persistent checkpoints.
"""

from .checkpoint import CheckpointManager
from .config import ExecutorType, ParallelConfig
from .models import Checkpoint, JobState, JobStatus, JobSummary
from .persistence import JobPersistence
from .processor import ParallelProcessor
from .result import BatchResult, FileResult
from .resume import ResumableProcessor
from .scheduler import PriorityStrategy, TaskScheduler

__all__ = [
    "BatchResult",
    "Checkpoint",
    "CheckpointManager",
    "ExecutorType",
    "FileResult",
    "JobPersistence",
    "JobState",
    "JobStatus",
    "JobSummary",
    "ParallelConfig",
    "ParallelProcessor",
    "PriorityStrategy",
    "ResumableProcessor",
    "TaskScheduler",
]

__version__ = "1.1.0"
