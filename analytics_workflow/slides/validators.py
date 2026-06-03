from __future__ import annotations

import os
from collections import Counter
from typing import Any

from .deck_spec import DeckSpec, SlideSpec
from .chart_renderer import SUPPORTED_CHART_TYPES, has_structured_chart_data
from .templates import ANALYSIS_TEMPLATE_SEQUENCE, REQUIRED_SLIDE_ROLES, is_supported_template
from .text_refiner import compact_whitespace, refine_bullets, refine_headline, shorten, soften_unsupported_impact_claim

MAX_BULLETS = 5
MAX_HEADLINE_CHARS = 96
MAX_MESSAGE_CHARS = 180
VISUAL_TEMPLATES = {
    "chart_left_insight_right",
    "chart_right_insight_left",
    "full_width_chart_takeaway",
    "metric_strip_plus_chart",
    "small_multiples_with_takeaway",
    "single_bar_chart_with_insight",
    "horizontal_bar_ranking",
    "metric_cards_with_chart",
    "comparison_chart_with_interpretation",
    "distribution_with_callout",
}


def validate_deck_spec(deck: DeckSpec) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    roles = [slide.slide_role for slide in deck.slides]
    if len(deck.slides) != 12:
        issues.append({"scope": "deck", "issue": "slide_count_not_12", "count": len(deck.slides)})
    for index, expected in enumerate(REQUIRED_SLIDE_ROLES, start=1):
        if len(roles) < index or roles[index - 1] != expected:
            issues.append({"scope": "deck", "issue": "missing_required_slide_role", "slide": index, "expected": expected})

    template_counts = Counter(slide.template for slide in deck.slides)
    for template, count in template_counts.items():
        if count > 4:
            issues.append({"scope": "deck", "issue": "template_repeated_too_often", "template": template, "count": count})

    for slide in deck.slides:
        issues.extend(validate_slide_spec(slide))
    return issues


def validate_slide_spec(slide: SlideSpec) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not is_supported_template(slide.template):
        issues.append({"scope": "slide", "slide": slide.slide_number, "issue": "unsupported_template"})
    if not slide.headline.strip():
        issues.append({"scope": "slide", "slide": slide.slide_number, "issue": "missing_headline"})
    if not slide.slide_role.strip():
        issues.append({"scope": "slide", "slide": slide.slide_number, "issue": "missing_slide_purpose"})
    if len(slide.headline) > MAX_HEADLINE_CHARS:
        issues.append({"scope": "slide", "slide": slide.slide_number, "issue": "headline_too_long"})
    if len(slide.main_message) > MAX_MESSAGE_CHARS:
        issues.append({"scope": "slide", "slide": slide.slide_number, "issue": "main_message_too_long"})
    if len(slide.bullets) > MAX_BULLETS:
        issues.append({"scope": "slide", "slide": slide.slide_number, "issue": "too_many_bullets"})
    if slide.visual and slide.visual.type in {"image", "code_figure"} and slide.visual.image_path and not os.path.exists(slide.visual.image_path):
        issues.append({"scope": "slide", "slide": slide.slide_number, "issue": "missing_visual_reference"})
    if slide.slide_role == "analysis" and not slide.visual:
        issues.append({"scope": "slide", "slide": slide.slide_number, "issue": "text_only_analysis_slide"})
    if slide.slide_role == "analysis" and slide.visual and not slide.visual.type:
        issues.append({"scope": "slide", "slide": slide.slide_number, "issue": "missing_visual_type"})
    if slide.slide_role == "analysis" and slide.visual and slide.visual.type in {"structured_chart", "chart", "native_chart"}:
        chart_type = slide.visual.chart_type.strip().lower()
        if chart_type not in SUPPORTED_CHART_TYPES:
            issues.append({"scope": "slide", "slide": slide.slide_number, "issue": "unsupported_chart_type", "chart_type": slide.visual.chart_type})
        if not has_structured_chart_data(slide.visual):
            issues.append({"scope": "slide", "slide": slide.slide_number, "issue": "chart_visual_missing_data", "chart_type": slide.visual.chart_type})
    if slide.slide_role == "analysis" and slide.visual and slide.visual.type in {"image", "legacy_image_fallback"}:
        issues.append(
            {
                "scope": "slide",
                "slide": slide.slide_number,
                "issue": "raw_eda_image_fallback_used",
                "visual_path": slide.visual.image_path,
                "fallback_reason": slide.visual.fallback_reason or "structured_chart_data_missing",
            }
        )
    if slide.slide_role == "analysis" and slide.visual and slide.visual.type in {"image", "legacy_image_fallback"} and "figure_" in os.path.basename(slide.visual.image_path).lower():
        issues.append({"scope": "slide", "slide": slide.slide_number, "issue": "code_generated_figure_used_as_fallback"})
    if slide.template in VISUAL_TEMPLATES and not slide.visual:
        issues.append({"scope": "slide", "slide": slide.slide_number, "issue": "missing_visual_reference"})
    slide_text = " ".join(
        [slide.headline, slide.main_message]
        + [item for block in slide.content_blocks for item in block.items]
    )
    if compact_whitespace(slide_text) != soften_unsupported_impact_claim(slide_text):
        issues.append({"scope": "slide", "slide": slide.slide_number, "issue": "unsupported_quantified_impact_claim"})
    return issues


def repair_deck_spec(deck: DeckSpec) -> DeckSpec:
    for index, slide in enumerate(deck.slides, start=1):
        repair_slide_spec(slide, index)

    analysis_slides = [slide for slide in deck.slides if slide.slide_role == "analysis"]
    for index, slide in enumerate(analysis_slides):
        if not is_supported_template(slide.template):
            slide.template = ANALYSIS_TEMPLATE_SEQUENCE[index % len(ANALYSIS_TEMPLATE_SEQUENCE)]
    deck.renumber()
    return deck


def repair_slide_spec(slide: SlideSpec, index: int) -> SlideSpec:
    if not is_supported_template(slide.template):
        slide.template = "full_width_chart_takeaway" if slide.slide_role == "analysis" and slide.visual else "three_finding_cards"
    slide.headline = refine_headline(soften_unsupported_impact_claim(slide.headline), f"Slide {index}", width=MAX_HEADLINE_CHARS)
    slide.main_message = shorten(
        soften_unsupported_impact_claim(slide.main_message or (slide.bullets[0] if slide.bullets else slide.headline)),
        MAX_MESSAGE_CHARS,
    )
    for block in slide.content_blocks:
        if block.type == "bullets":
            block.items = refine_bullets(block.items, max_items=MAX_BULLETS)
        elif block.type in {"recommendations", "limitations"}:
            max_chars = 240 if block.type == "recommendations" else MAX_MESSAGE_CHARS
            block.items = [
                shorten(soften_unsupported_impact_claim(item), max_chars, placeholder="")
                for item in block.items[:MAX_BULLETS]
            ]
    if slide.visual and slide.visual.type in {"image", "code_figure"} and slide.visual.image_path and not os.path.exists(slide.visual.image_path):
        slide.visual = None
    if slide.visual is None and slide.template in VISUAL_TEMPLATES:
        slide.template = "three_finding_cards" if slide.bullets else "comparison_matrix"
    return slide
