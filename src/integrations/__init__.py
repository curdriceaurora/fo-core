"""Third-party integration framework package."""

from integrations.base import (
    Integration,
    IntegrationConfig,
    IntegrationStatus,
    IntegrationType,
)
from integrations.browser import BrowserExtensionManager
from integrations.manager import IntegrationManager
from integrations.obsidian import ObsidianIntegration
from integrations.vscode import VSCodeIntegration
from integrations.workflow import WorkflowIntegration

__all__ = [
    "BrowserExtensionManager",
    "Integration",
    "IntegrationConfig",
    "IntegrationManager",
    "IntegrationStatus",
    "IntegrationType",
    "ObsidianIntegration",
    "VSCodeIntegration",
    "WorkflowIntegration",
]
