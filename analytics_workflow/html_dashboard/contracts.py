from __future__ import annotations

from enum import Enum
from typing import Any


class DashboardErrorCode(str, Enum):
    INVALID_PLAN = "HTML_DASHBOARD_INVALID_PLAN"
    MISSING_FIELD = "HTML_DASHBOARD_MISSING_FIELD"
    TOO_MANY_ROWS = "HTML_DASHBOARD_TOO_MANY_ROWS"
    PATH_OUTSIDE_RUN = "HTML_DASHBOARD_PATH_OUTSIDE_RUN"
    RENDER_FAILED = "HTML_DASHBOARD_RENDER_FAILED"
    QA_FAILED = "HTML_DASHBOARD_QA_FAILED"
    STAGE_TIMEOUT = "HTML_DASHBOARD_STAGE_TIMEOUT"


class DashboardWorkflowError(RuntimeError):
    def __init__(
        self,
        code: DashboardErrorCode | str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code.value if isinstance(code, DashboardErrorCode) else str(code)
        self.retryable = retryable
        self.details = details or {}
        super().__init__(f"{self.code}: {message}")


DASHBOARD_PLAN_SCHEMA: dict[str, Any] = {
    "project_name": "string",
    "title": "string",
    "subtitle": "string",
    "filters": [{"dataset": "string", "field": "string", "label": "string"}],
    "kpis": [
        {
            "dataset": "string",
            "label": "string",
            "field": "string",
            "aggregation": "count|count_distinct|sum|mean|min|max",
            "format": "number|integer|currency|percent",
            "description": "string",
        }
    ],
    "pages": [
        {
            "name": "string",
            "purpose": "string",
            "charts": [
                {
                    "dataset": "string",
                    "title": "string",
                    "type": "bar|line|scatter|histogram",
                    "x": "string",
                    "y": "string",
                    "aggregation": "count|sum|mean|min|max|none",
                    "description": "string",
                }
            ],
            "table": {"dataset": "string", "title": "string", "columns": ["string"]},
        }
    ],
    "theme": {"accent": "hex color", "background": "hex color"},
    "source_notes": ["string"],
}
