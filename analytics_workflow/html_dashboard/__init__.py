"""Self-contained HTML dashboard workflow components."""

from .contracts import DashboardErrorCode, DashboardWorkflowError
from .workflow import HTMLDashboardWorkflow

__all__ = ["DashboardErrorCode", "DashboardWorkflowError", "HTMLDashboardWorkflow"]
