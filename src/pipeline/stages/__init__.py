"""Composable pipeline stages for the auto-organization pipeline.

Each stage implements :class:`~interfaces.PipelineStage`
and can be composed into a pipeline via the orchestrator.

Stages:
    PreprocessorStage: File validation and metadata extraction.
    AnalyzerStage: LLM-based content analysis.
    PostprocessorStage: Destination path computation.
    WriterStage: File copy/move operations.
"""

from __future__ import annotations

from pipeline.stages.analyzer import AnalyzerStage
from pipeline.stages.postprocessor import PostprocessorStage
from pipeline.stages.preprocessor import PreprocessorStage
from pipeline.stages.writer import WriterStage

__all__ = [
    "AnalyzerStage",
    "PostprocessorStage",
    "PreprocessorStage",
    "WriterStage",
]
