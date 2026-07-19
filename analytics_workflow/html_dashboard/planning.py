from __future__ import annotations

import json
import re
import warnings
from pathlib import Path
from typing import Any

import pandas as pd

from ..clients import OpenRouterClient
from .contracts import DASHBOARD_PLAN_SCHEMA, DashboardErrorCode, DashboardWorkflowError


SUPPORTED_AGGREGATIONS = {"count", "count_distinct", "sum", "mean", "min", "max"}
SUPPORTED_CHART_TYPES = {"bar", "line", "scatter", "histogram"}


class HTMLDashboardPlanningAgent:
    """Read-only planner with no filesystem, browser, export, or MCP capabilities."""

    def __init__(self, client: OpenRouterClient, timeout_seconds: int) -> None:
        self.client = client
        self.timeout_seconds = max(1, int(timeout_seconds))

    def execute(self, workflow_state: dict[str, Any]) -> dict[str, Any]:
        csv_data = workflow_state.get("csv_data", {})
        profiles = build_dataset_profiles(csv_data)
        evidence = {
            "objective": workflow_state.get("workflow_objective", {}),
            "data_understanding": workflow_state.get("agent_outputs", {}).get("data_understander", {}),
            "dataset_profiles": profiles,
        }
        system = (
            "You are the isolated HTML Dashboard Planning Agent. Design a concise, decision-useful, offline dashboard "
            "from only the supplied datasets. Return the complete schema. Use at most 3 filters, 6 KPI cards, 3 pages, "
            "and 2 charts per page. Prefer business-readable labels. Use count for categorical-only breakdowns, mean or "
            "sum only for real numeric measures, line charts only for time/order fields, and scatter only for two numeric "
            "fields. Do not invent columns, values, findings, targets, credentials, external sources, or executable code. "
            "The renderer computes every value from the local CSV and creates a self-contained HTML file."
        )
        candidate = self.client.chat_completion_json(
            system,
            json.dumps(evidence, default=str)[:60000],
            DASHBOARD_PLAN_SCHEMA,
            timeout_seconds=self.timeout_seconds,
            max_tokens=8000,
            reasoning_effort="none",
        )
        return normalize_dashboard_plan(candidate, csv_data, workflow_state.get("workflow_objective", {}))


def build_dataset_profiles(csv_data: dict[str, pd.DataFrame]) -> dict[str, Any]:
    profiles: dict[str, Any] = {}
    for name, frame in csv_data.items():
        columns: list[dict[str, Any]] = []
        for column in frame.columns:
            series = frame[column]
            role = _column_role(series)
            columns.append(
                {
                    "name": str(column),
                    "dtype": str(series.dtype),
                    "role": role,
                    "missing_pct": round(float(series.isna().mean() * 100), 2),
                    "unique_count": int(series.nunique(dropna=True)),
                }
            )
        profiles[name] = {"rows": len(frame), "columns": columns}
    return profiles


def normalize_dashboard_plan(
    candidate: dict[str, Any],
    csv_data: dict[str, pd.DataFrame],
    objective: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not csv_data:
        raise DashboardWorkflowError(DashboardErrorCode.MISSING_FIELD, "No loaded CSV data is available.")
    datasets = list(csv_data)
    columns = {name: [str(column) for column in frame.columns] for name, frame in csv_data.items()}
    default_dataset = datasets[0]
    title = str(candidate.get("title", "")).strip() or _default_title(default_dataset)
    subtitle = str(candidate.get("subtitle", "")).strip()
    if not subtitle:
        question = str((objective or {}).get("decision_question", "")).strip()
        subtitle = question or "Interactive summary of the uploaded data"

    filters: list[dict[str, str]] = []
    seen_filters: set[tuple[str, str]] = set()
    for item in candidate.get("filters", []) or []:
        if not isinstance(item, dict):
            continue
        dataset = str(item.get("dataset", default_dataset))
        field = str(item.get("field", ""))
        if dataset not in csv_data or field not in columns[dataset] or (dataset, field) in seen_filters:
            continue
        series = csv_data[dataset][field]
        unique = int(series.nunique(dropna=True))
        if _column_role(series) not in {"categorical", "boolean"} or not 2 <= unique <= 100:
            continue
        filters.append({"dataset": dataset, "field": field, "label": str(item.get("label", field)).strip() or field})
        seen_filters.add((dataset, field))
        if len(filters) == 3:
            break
    if not filters:
        filters = _fallback_filters(csv_data, limit=3)

    kpis: list[dict[str, str]] = []
    for item in candidate.get("kpis", []) or []:
        normalized = _normalize_kpi(item, csv_data, default_dataset)
        if normalized and normalized not in kpis:
            kpis.append(normalized)
        if len(kpis) == 6:
            break
    if not kpis:
        kpis = _fallback_kpis(csv_data)

    pages: list[dict[str, Any]] = []
    seen_chart_titles: set[str] = set()
    for page_index, page in enumerate(candidate.get("pages", []) or []):
        if not isinstance(page, dict):
            continue
        charts: list[dict[str, str]] = []
        for item in page.get("charts", []) or []:
            chart = _normalize_chart(item, csv_data, default_dataset)
            if not chart or chart["title"] in seen_chart_titles:
                continue
            charts.append(chart)
            seen_chart_titles.add(chart["title"])
            if len(charts) == 2:
                break
        table = _normalize_table(page.get("table", {}), csv_data, default_dataset)
        if charts or table:
            pages.append(
                {
                    "name": str(page.get("name", f"Page {page_index + 1}")).strip() or f"Page {page_index + 1}",
                    "purpose": str(page.get("purpose", "")).strip(),
                    "charts": charts,
                    "table": table,
                }
            )
        if len(pages) == 3:
            break
    if not any(page["charts"] for page in pages):
        fallback_charts = _fallback_charts(csv_data)
        pages = [
            {
                "name": f"Overview {index // 2 + 1}" if len(fallback_charts) > 2 else "Overview",
                "purpose": "Primary patterns and breakdowns",
                "charts": fallback_charts[index : index + 2],
                "table": _fallback_table(csv_data) if index == 0 else {},
            }
            for index in range(0, len(fallback_charts), 2)
        ][:3]
    elif pages and not any(page.get("table") for page in pages):
        pages[0]["table"] = _fallback_table(csv_data)

    theme = candidate.get("theme", {}) if isinstance(candidate.get("theme"), dict) else {}
    accent = _safe_hex(str(theme.get("accent", "")), "#2563eb")
    background = _safe_hex(str(theme.get("background", "")), "#f4f7fb")
    notes = [str(note).strip() for note in candidate.get("source_notes", []) or [] if str(note).strip()][:5]
    return {
        "project_name": safe_project_name(str(candidate.get("project_name", title))),
        "title": title[:120],
        "subtitle": subtitle[:240],
        "filters": filters,
        "kpis": kpis,
        "pages": pages,
        "theme": {"accent": accent, "background": background},
        "source_notes": notes,
    }


def safe_project_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value or "HTML_Dashboard").strip("_")
    return (cleaned or "HTML_Dashboard")[:80]


def _normalize_kpi(item: Any, csv_data: dict[str, pd.DataFrame], default_dataset: str) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    dataset = str(item.get("dataset", default_dataset))
    field = str(item.get("field", ""))
    aggregation = str(item.get("aggregation", "count")).lower()
    if dataset not in csv_data or aggregation not in SUPPORTED_AGGREGATIONS:
        return None
    if field not in csv_data[dataset].columns:
        if aggregation == "count" and len(csv_data[dataset].columns):
            field = str(csv_data[dataset].columns[0])
        else:
            return None
    if aggregation in {"sum", "mean", "min", "max"} and not pd.api.types.is_numeric_dtype(csv_data[dataset][field]):
        return None
    value_format = str(item.get("format", "number")).lower()
    if value_format not in {"number", "integer", "currency", "percent"}:
        value_format = "number"
    return {
        "dataset": dataset,
        "label": (str(item.get("label", "")).strip() or _humanize(f"{aggregation} {field}"))[:80],
        "field": field,
        "aggregation": aggregation,
        "format": value_format,
        "description": str(item.get("description", "")).strip()[:180],
    }


def _normalize_chart(item: Any, csv_data: dict[str, pd.DataFrame], default_dataset: str) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    dataset = str(item.get("dataset", default_dataset))
    if dataset not in csv_data:
        return None
    frame = csv_data[dataset]
    chart_type = str(item.get("type", "bar")).lower()
    x = str(item.get("x", ""))
    y = str(item.get("y", ""))
    aggregation = str(item.get("aggregation", "count")).lower()
    if chart_type not in SUPPORTED_CHART_TYPES or x not in frame.columns:
        return None
    if chart_type == "histogram":
        if not pd.api.types.is_numeric_dtype(frame[x]):
            return None
        y, aggregation = "", "count"
    elif chart_type == "scatter":
        if y not in frame.columns or not pd.api.types.is_numeric_dtype(frame[x]) or not pd.api.types.is_numeric_dtype(frame[y]):
            return None
        aggregation = "none"
    else:
        if aggregation not in {"count", "sum", "mean", "min", "max"}:
            return None
        if aggregation != "count" and (y not in frame.columns or not pd.api.types.is_numeric_dtype(frame[y])):
            return None
        if aggregation == "count":
            y = ""
        if chart_type == "line" and _column_role(frame[x]) not in {"datetime", "numeric"}:
            return None
    label = str(item.get("title", "")).strip() or _humanize(f"{aggregation} {y or 'records'} by {x}")
    return {
        "dataset": dataset,
        "title": label[:100],
        "type": chart_type,
        "x": x,
        "y": y,
        "aggregation": aggregation,
        "description": str(item.get("description", "")).strip()[:180],
    }


def _normalize_table(item: Any, csv_data: dict[str, pd.DataFrame], default_dataset: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    dataset = str(item.get("dataset", default_dataset))
    if dataset not in csv_data:
        return {}
    valid = [str(column) for column in item.get("columns", []) or [] if str(column) in csv_data[dataset].columns][:10]
    if not valid:
        return {}
    return {"dataset": dataset, "title": str(item.get("title", "Detail")).strip() or "Detail", "columns": valid}


def _fallback_filters(csv_data: dict[str, pd.DataFrame], *, limit: int) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for dataset, frame in csv_data.items():
        for column in frame.columns:
            unique = int(frame[column].nunique(dropna=True))
            if _column_role(frame[column]) in {"categorical", "boolean"} and 2 <= unique <= 30:
                items.append({"dataset": dataset, "field": str(column), "label": _humanize(str(column))})
                if len(items) == limit:
                    return items
    return items


def _fallback_kpis(csv_data: dict[str, pd.DataFrame]) -> list[dict[str, str]]:
    dataset, frame = next(iter(csv_data.items()))
    first = str(frame.columns[0])
    items = [{"dataset": dataset, "label": "Total records", "field": first, "aggregation": "count", "format": "integer", "description": "Rows in the selected view"}]
    for column in frame.select_dtypes(include="number").columns[:3]:
        items.append({"dataset": dataset, "label": f"Average {_humanize(str(column))}", "field": str(column), "aggregation": "mean", "format": "number", "description": "Mean for the selected view"})
    return items


def _fallback_charts(csv_data: dict[str, pd.DataFrame]) -> list[dict[str, str]]:
    charts: list[dict[str, str]] = []
    for dataset, frame in csv_data.items():
        numeric = [str(column) for column in frame.select_dtypes(include="number").columns]
        categorical = [str(column) for column in frame.columns if _column_role(frame[column]) in {"categorical", "boolean"} and frame[column].nunique(dropna=True) <= 30]
        datetime = [str(column) for column in frame.columns if _column_role(frame[column]) == "datetime"]
        if categorical:
            charts.append({"dataset": dataset, "title": f"Records by {_humanize(categorical[0])}", "type": "bar", "x": categorical[0], "y": "", "aggregation": "count", "description": "Distribution of records"})
        if categorical and numeric:
            charts.append({"dataset": dataset, "title": f"Average {_humanize(numeric[0])} by {_humanize(categorical[0])}", "type": "bar", "x": categorical[0], "y": numeric[0], "aggregation": "mean", "description": "Segment comparison"})
        if datetime and numeric:
            charts.append({"dataset": dataset, "title": f"{_humanize(numeric[0])} over time", "type": "line", "x": datetime[0], "y": numeric[0], "aggregation": "mean", "description": "Time trend"})
        elif len(numeric) >= 2:
            charts.append({"dataset": dataset, "title": f"{_humanize(numeric[1])} vs {_humanize(numeric[0])}", "type": "scatter", "x": numeric[0], "y": numeric[1], "aggregation": "none", "description": "Numeric relationship"})
        elif numeric:
            charts.append({"dataset": dataset, "title": f"Distribution of {_humanize(numeric[0])}", "type": "histogram", "x": numeric[0], "y": "", "aggregation": "count", "description": "Numeric distribution"})
        if len(charts) >= 4:
            break
    if not charts:
        dataset, frame = next(iter(csv_data.items()))
        first = str(frame.columns[0])
        charts.append({"dataset": dataset, "title": f"Records by {_humanize(first)}", "type": "bar", "x": first, "y": "", "aggregation": "count", "description": "Distribution of records"})
    return charts[:4]


def _fallback_table(csv_data: dict[str, pd.DataFrame]) -> dict[str, Any]:
    dataset, frame = next(iter(csv_data.items()))
    return {"dataset": dataset, "title": "Record detail", "columns": [str(column) for column in frame.columns[:8]]}


def _column_role(series: pd.Series) -> str:
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    non_null = series.dropna().astype(str).str.strip()
    if not non_null.empty:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            if pd.to_datetime(non_null.head(200), errors="coerce").notna().mean() >= 0.9:
                return "datetime"
    if int(series.nunique(dropna=True)) <= 2:
        return "boolean"
    return "categorical"


def _safe_hex(value: str, fallback: str) -> str:
    return value if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else fallback


def _humanize(value: str) -> str:
    return re.sub(r"[_-]+", " ", re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)).strip().title()


def _default_title(dataset: str) -> str:
    return _humanize(Path(dataset).stem) + " Dashboard"
