from __future__ import annotations

from enum import Enum
from typing import Callable


class OutputPath(str, Enum):
    ANALYTICS_REPORT = "analytics_report"
    HTML_DASHBOARD = "html_dashboard"


OUTPUT_PATH_MENU = """Choose an output path:

1. Analytics Report
   - PDF
   - PowerPoint

2. BI Dashboard (HTML)
   - self-contained HTML file
   - interactive filters and charts
   - works offline in a browser"""


def coerce_output_path(value: OutputPath | str) -> OutputPath:
    if isinstance(value, OutputPath):
        return value
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "1": OutputPath.ANALYTICS_REPORT,
        "analytics": OutputPath.ANALYTICS_REPORT,
        "analytics_report": OutputPath.ANALYTICS_REPORT,
        "report": OutputPath.ANALYTICS_REPORT,
        "2": OutputPath.HTML_DASHBOARD,
        "dashboard": OutputPath.HTML_DASHBOARD,
        "html": OutputPath.HTML_DASHBOARD,
        "html_dashboard": OutputPath.HTML_DASHBOARD,
        "bi_dashboard": OutputPath.HTML_DASHBOARD,
        # Hidden compatibility aliases for checkpoints created before the
        # runtime Power BI integration was removed.
        "powerbi": OutputPath.HTML_DASHBOARD,
        "power_bi": OutputPath.HTML_DASHBOARD,
        "power_bi_dashboard": OutputPath.HTML_DASHBOARD,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError("output_path must be 'analytics_report' or 'html_dashboard'.") from exc


def prompt_output_path(
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
    max_attempts: int = 3,
) -> OutputPath:
    print_fn(OUTPUT_PATH_MENU)
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        raw = input_fn("Selection [1/2]: ")
        try:
            return coerce_output_path(raw)
        except ValueError:
            remaining = max_attempts - attempt
            if remaining:
                print_fn(f"Invalid selection. Choose 1 or 2 ({remaining} attempt(s) remaining).")
    raise ValueError("No valid output path selected after 3 attempts.")
