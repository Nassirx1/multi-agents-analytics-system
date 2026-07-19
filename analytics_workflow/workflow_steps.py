from __future__ import annotations


ANALYTICS_REPORT_STEPS = [
    "Data Understander",
    "Market Researcher",
    "Analysis Planner",
    "Data Scientist Coder",
    "Code Reviewer",
    "Business Translator",
    "Decision Maker",
    "PDF Report Generator",
    "Slide Deck Generator",
]

HTML_DASHBOARD_STEPS = [
    "Data Understander",
    "Dashboard Planning Agent",
    "HTML Dashboard Generator",
    "HTML Dashboard QA Agent",
]

# Backward-compatible alias for existing report-path imports and tests.
WORKFLOW_STEPS = ANALYTICS_REPORT_STEPS


def workflow_steps_for(output_path: object) -> list[str]:
    value = getattr(output_path, "value", output_path)
    return list(HTML_DASHBOARD_STEPS if value == "html_dashboard" else ANALYTICS_REPORT_STEPS)


def format_step_update(step_number: int, step_name: str, status: str, *, total_steps: int | None = None) -> str:
    total = len(WORKFLOW_STEPS) if total_steps is None else total_steps
    return f"[{status}] Step {step_number}/{total}: {step_name}"
