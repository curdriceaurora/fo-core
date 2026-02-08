"""
Optimization module for the file organizer system.

This module provides database indexing, query optimization, connection pooling,
query caching, lazy model loading, model caching, resource monitoring, and
model warmup capabilities.
"""
from __future__ import annotations

from .connection_pool import ConnectionPool, PoolStats
from .database import DatabaseOptimizer, QueryPlan, TableStats
from .lazy_loader import LazyModelLoader
from .model_cache import CacheStats, ModelCache
from .query_cache import CachedResult, QueryCache
from .resource_monitor import GpuMemoryInfo, MemoryInfo, ResourceMonitor
from .warmup import ModelWarmup, WarmupResult

__all__ = [
    "CachedResult",
    "CacheStats",
    "ConnectionPool",
    "DatabaseOptimizer",
    "GpuMemoryInfo",
    "LazyModelLoader",
    "MemoryInfo",
    "ModelCache",
    "ModelWarmup",
    "PoolStats",
    "QueryCache",
    "QueryPlan",
    "ResourceMonitor",
    "TableStats",
    "WarmupResult",
]

__version__ = "1.0.0"
