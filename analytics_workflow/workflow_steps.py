from __future__ import annotations


WORKFLOW_STEPS = [
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


def format_step_update(step_number: int, step_name: str, status: str) -> str:
    return f"[{status}] Step {step_number}/{len(WORKFLOW_STEPS)}: {step_name}"
