from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlideTemplate:
    name: str
    description: str
    supports_visual: bool = False


TEMPLATE_REGISTRY: dict[str, SlideTemplate] = {
    "title_cover": SlideTemplate("title_cover", "Cover with title, subtitle, metadata, and a minimal accent."),
    "data_understanding_overview": SlideTemplate(
        "data_understanding_overview",
        "Dataset summary cards, key fields, target, size, and quality notes.",
    ),
    "market_context_bullets": SlideTemplate(
        "market_context_bullets",
        "Headline with simple market or domain context bullets and a why-it-matters box.",
    ),
    "chart_left_insight_right": SlideTemplate(
        "chart_left_insight_right",
        "Chart on the left with insight and short interpretation on the right.",
        supports_visual=True,
    ),
    "chart_right_insight_left": SlideTemplate(
        "chart_right_insight_left",
        "Insight on the left with chart on the right.",
        supports_visual=True,
    ),
    "full_width_chart_takeaway": SlideTemplate(
        "full_width_chart_takeaway",
        "Large chart with a concise takeaway strip.",
        supports_visual=True,
    ),
    "metric_strip_plus_chart": SlideTemplate(
        "metric_strip_plus_chart",
        "Three to four metric cards above a main chart.",
        supports_visual=True,
    ),
    "small_multiples_with_takeaway": SlideTemplate(
        "small_multiples_with_takeaway",
        "Readable mini charts rebuilt from structured series data with a clear takeaway.",
        supports_visual=True,
    ),
    "single_bar_chart_with_insight": SlideTemplate(
        "single_bar_chart_with_insight",
        "Single reconstructed bar chart with an insight panel.",
        supports_visual=True,
    ),
    "horizontal_bar_ranking": SlideTemplate(
        "horizontal_bar_ranking",
        "Theme-matched horizontal ranking chart with interpretation.",
        supports_visual=True,
    ),
    "metric_cards_with_chart": SlideTemplate(
        "metric_cards_with_chart",
        "Metric cards paired with a reconstructed chart.",
        supports_visual=True,
    ),
    "comparison_chart_with_interpretation": SlideTemplate(
        "comparison_chart_with_interpretation",
        "Comparison chart with concise interpretation.",
        supports_visual=True,
    ),
    "distribution_with_callout": SlideTemplate(
        "distribution_with_callout",
        "Distribution visual with a clear callout.",
        supports_visual=True,
    ),
    "segment_profile_cards": SlideTemplate(
        "segment_profile_cards",
        "Segment profile cards created from structured analysis output.",
    ),
    "three_finding_cards": SlideTemplate(
        "three_finding_cards",
        "Three finding cards with evidence and implication.",
    ),
    "comparison_matrix": SlideTemplate(
        "comparison_matrix",
        "2x2 or side-by-side comparison for segments, groups, or categories.",
    ),
    "recommendation_priority": SlideTemplate(
        "recommendation_priority",
        "Recommendation cards with action, rationale, impact, and priority.",
    ),
    "limitations_professional": SlideTemplate(
        "limitations_professional",
        "Professional limitation cards with mitigation or next step.",
    ),
    "executive_summary_closing": SlideTemplate(
        "executive_summary_closing",
        "Final executive message, key takeaways, and recommended next action.",
    ),
}


LEGACY_LAYOUT_TO_TEMPLATE = {
    "cover": "title_cover",
    "executive_summary": "three_finding_cards",
    "dataset_overview": "data_understanding_overview",
    "kpi_cards": "metric_strip_plus_chart",
    "chart_focus": "full_width_chart_takeaway",
    "chart_with_takeaways": "chart_left_insight_right",
    "two_column_insight": "comparison_matrix",
    "insight_cards": "three_finding_cards",
    "recommendation_matrix": "recommendation_priority",
    "risk_limitations": "limitations_professional",
    "closing": "executive_summary_closing",
}


ANALYSIS_TEMPLATE_SEQUENCE = [
    "single_bar_chart_with_insight",
    "horizontal_bar_ranking",
    "comparison_chart_with_interpretation",
    "metric_cards_with_chart",
    "segment_profile_cards",
]


REQUIRED_SLIDE_ROLES = [
    "title",
    "data_understanding",
    "market_context",
    "analysis",
    "analysis",
    "analysis",
    "analysis",
    "findings",
    "business_translation",
    "recommendations",
    "limitations",
    "summary",
]


def is_supported_template(template: str) -> bool:
    return template in TEMPLATE_REGISTRY


def template_for_legacy_layout(layout_type: str) -> str:
    return LEGACY_LAYOUT_TO_TEMPLATE.get(layout_type, "three_finding_cards")
