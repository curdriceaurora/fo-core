"""Intelligence services for learning user preferences and patterns.

This module provides intelligent learning capabilities that adapt to user
behavior and preferences over time.
"""

from __future__ import annotations

from services.intelligence.confidence import (
    ConfidenceEngine,
    PatternUsageData,
    UsageRecord,
)
from services.intelligence.conflict_resolver import ConflictResolver
from services.intelligence.directory_prefs import DirectoryPrefs
from services.intelligence.feedback_processor import FeedbackProcessor
from services.intelligence.folder_learner import FolderPreferenceLearner
from services.intelligence.naming_analyzer import (
    NameStructure,
    NamingAnalyzer,
)
from services.intelligence.pattern_extractor import (
    NamingPattern,
    NamingPatternExtractor,
    PatternElement,
)
from services.intelligence.pattern_learner import PatternLearner
from services.intelligence.preference_storage import (
    InMemoryPreferenceStorage,
    PreferenceStorage,
    SqlitePreferenceStorage,
)
from services.intelligence.preference_store import (
    DirectoryPreference,
    PreferenceStore,
    SchemaVersion,
)
from services.intelligence.preference_tracker import (
    Correction,
    CorrectionType,
    Preference,
    PreferenceMetadata,
    PreferenceTracker,
    PreferenceType,
    create_tracker,
    track_category_change,
    track_file_move,
    track_file_rename,
)
from services.intelligence.profile_exporter import ProfileExporter
from services.intelligence.profile_importer import ProfileImporter, ValidationResult
from services.intelligence.profile_manager import Profile, ProfileManager
from services.intelligence.profile_merger import MergeStrategy, ProfileMerger
from services.intelligence.profile_migrator import ProfileMigrator
from services.intelligence.scoring import (
    PatternScorer,
    ScoreAnalyzer,
    ScoredPattern,
)
from services.intelligence.template_manager import TemplateManager

__all__ = [
    "PreferenceTracker",
    "Preference",
    "PreferenceType",
    "PreferenceMetadata",
    "Correction",
    "CorrectionType",
    "create_tracker",
    "track_file_move",
    "track_file_rename",
    "track_category_change",
    "PreferenceStorage",
    "InMemoryPreferenceStorage",
    "SqlitePreferenceStorage",
    "PreferenceStore",
    "DirectoryPreference",
    "SchemaVersion",
    "DirectoryPrefs",
    "ConflictResolver",
    "ConfidenceEngine",
    "UsageRecord",
    "PatternUsageData",
    "PatternScorer",
    "ScoredPattern",
    "ScoreAnalyzer",
    "NamingPatternExtractor",
    "NamingPattern",
    "PatternElement",
    "NamingAnalyzer",
    "NameStructure",
    "FolderPreferenceLearner",
    "FeedbackProcessor",
    "PatternLearner",
    "Profile",
    "ProfileManager",
    "ProfileExporter",
    "ProfileImporter",
    "ValidationResult",
    "ProfileMigrator",
    "ProfileMerger",
    "MergeStrategy",
    "TemplateManager",
]
