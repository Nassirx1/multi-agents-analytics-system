from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

try:
    from pptx import Presentation  # noqa: F401

    PPTX_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    PPTX_AVAILABLE = False

from .slides import build_deck_spec, render_deck
from .slides.templates import LEGACY_LAYOUT_TO_TEMPLATE, TEMPLATE_REGISTRY
from .slides.text_refiner import refine_bullets, refine_headline, shorten


ALLOWED_LAYOUT_TYPES = set(LEGACY_LAYOUT_TO_TEMPLATE) | set(TEMPLATE_REGISTRY)
MAX_TITLE_CHARS = 96
MAX_BULLETS = 5
MAX_DETAIL_CHARS = 150


def validate_slide(slide: dict[str, Any], saved_figures: list[str] | None = None) -> list[str]:
    issues: list[str] = []
    template_or_layout = slide.get("template") or slide.get("layout_type")
    if template_or_layout not in ALLOWED_LAYOUT_TYPES:
        issues.append("invalid_layout_type")
    if len(str(slide.get("headline") or slide.get("title") or "")) > MAX_TITLE_CHARS:
        issues.append("title_too_long")
    if not str(slide.get("main_message", "")).strip():
        issues.append("missing_main_message")
    details = slide.get("details", []) or []
    if isinstance(details, list) and len(details) > MAX_BULLETS:
        issues.append("too_many_bullets")
    if isinstance(details, list) and any(len(str(detail)) > MAX_DETAIL_CHARS for detail in details):
        issues.append("details_too_verbose")
    visual_path = str(slide.get("visual_path") or slide.get("visual_element") or "").strip()
    if visual_path and saved_figures is not None and visual_path not in saved_figures:
        issues.append("missing_visual_file")
    return issues


def repair_slide(slide: dict[str, Any], issues: list[str], index: int) -> dict[str, Any]:
    repaired = dict(slide)
    if "invalid_layout_type" in issues:
        repaired["layout_type"] = "three_finding_cards"
    title = repaired.get("headline") or repaired.get("title") or f"Slide {index + 1}"
    repaired["title"] = refine_headline(title, f"Slide {index + 1}", width=MAX_TITLE_CHARS)
    repaired["headline"] = repaired["title"]
    if "missing_main_message" in issues:
        details = repaired.get("details", []) or []
        repaired["main_message"] = shorten(details[0] if details else repaired["title"], 170)
    if "too_many_bullets" in issues or "details_too_verbose" in issues:
        repaired["details"] = refine_bullets(repaired.get("details", []), max_items=MAX_BULLETS, max_chars=MAX_DETAIL_CHARS)
    if "missing_visual_file" in issues:
        repaired["visual_path"] = ""
        repaired["visual_element"] = ""
    return repaired


def normalize_slide_plan(workflow_state: dict[str, Any]) -> list[dict[str, Any]]:
    deck = build_deck_spec(workflow_state)
    # Legacy callers historically received planned content slides, not the cover.
    return [slide.to_legacy_dict() for slide in deck.slides if slide.slide_role != "title"]


def build_consulting_deck(workflow_state: dict[str, Any], output_path: str = "analytics_report.pptx") -> str:
    deck = build_deck_spec(workflow_state)
    for warning in workflow_state.get("slide_validation_warnings", []) or []:
        if isinstance(warning, dict) and warning.get("scope") in {"slide", "deck"}:
            try:
                logging.getLogger("SlideDeckGenerator").warning("Slide validation warning: %s", warning)
            except OSError:
                pass
    deck_path = render_deck(deck, output_path)
    _save_deck_artifacts(workflow_state, deck_path, deck.to_dict())
    return deck_path


def _save_deck_artifacts(workflow_state: dict[str, Any], deck_path: str, deck_payload: dict[str, Any]) -> None:
    output_dir = Path(deck_path).parent
    slide_plan_path = output_dir / "slide_plan.json"
    chart_specs_path = output_dir / "chart_specs.json"
    analysis_results = workflow_state.get("analysis_results", {}) or {}
    chart_payload = {
        "artifact_type": "chart_specs",
        "deck_path": deck_path,
        "chart_specs": analysis_results.get("chart_specs", []) or [],
        "analysis_artifacts": analysis_results.get("analysis_artifacts", []) or [],
        "saved_figures": workflow_state.get("saved_figures", []) or [],
        "slide_visuals": [
            {
                "slide_number": slide.get("slide_number"),
                "slide_role": slide.get("slide_role"),
                "template": slide.get("template"),
                "visual": slide.get("visual"),
            }
            for slide in deck_payload.get("slides", [])
            if slide.get("visual")
        ],
    }
    slide_plan_path.write_text(json.dumps(deck_payload, indent=2, default=str), encoding="utf-8")
    chart_specs_path.write_text(json.dumps(chart_payload, indent=2, default=str), encoding="utf-8")
    workflow_state.setdefault("generated_reports", {})["slide_plan"] = str(slide_plan_path)
    workflow_state.setdefault("generated_reports", {})["chart_specs"] = str(chart_specs_path)
