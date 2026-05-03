from __future__ import annotations

import json
import os
import re
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

try:
    from PIL import Image as PILImage
except ImportError:  # pragma: no cover - optional dependency
    PILImage = None

try:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt

    PPTX_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    PPTX_AVAILABLE = False


ALLOWED_LAYOUT_TYPES = {
    "cover",
    "executive_summary",
    "dataset_overview",
    "kpi_cards",
    "chart_focus",
    "chart_with_takeaways",
    "two_column_insight",
    "insight_cards",
    "recommendation_matrix",
    "risk_limitations",
    "closing",
}

VISUAL_LAYOUTS = {"chart_focus", "chart_with_takeaways"}
MAX_TITLE_CHARS = 95
MAX_BULLETS = 4
MAX_DETAIL_CHARS = 150


@dataclass(frozen=True)
class DeckTheme:
    width: float = 13.33
    height: float = 7.5
    margin_left: float = 0.72
    margin_right: float = 0.62
    footer_y: float = 7.03
    font_family: str = "Calibri"
    navy: tuple[int, int, int] = (18, 43, 69)
    blue: tuple[int, int, int] = (31, 94, 168)
    light_blue: tuple[int, int, int] = (224, 235, 247)
    sand: tuple[int, int, int] = (246, 243, 236)
    white: tuple[int, int, int] = (255, 255, 255)
    charcoal: tuple[int, int, int] = (36, 46, 56)
    slate: tuple[int, int, int] = (85, 96, 110)
    gold: tuple[int, int, int] = (200, 155, 60)
    pale_gold: tuple[int, int, int] = (244, 231, 199)
    green: tuple[int, int, int] = (55, 125, 93)
    red: tuple[int, int, int] = (176, 72, 72)


THEME = DeckTheme()


def _rgb(value: tuple[int, int, int]) -> Any:
    return RGBColor(*value)


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, indent=2, default=str)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return [value]
    text = _stringify(value)
    return [text] if text else []


def _shorten(text: Any, width: int, placeholder: str = "...") -> str:
    clean = re.sub(r"\s+", " ", _stringify(text)).strip()
    if not clean:
        return ""
    return textwrap.shorten(clean, width=width, placeholder=placeholder)


def _preferred_output_path(output_path: str) -> str:
    path = Path(output_path)
    if not path.exists():
        return str(path)
    try:
        with open(path, "ab"):
            return str(path)
    except OSError:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return str(path.with_name(f"{path.stem}_{timestamp}{path.suffix}"))


def _fit_image_size(path: str, max_width: float, max_height: float) -> tuple[float, float]:
    if PILImage is None:
        return max_width, max_height
    try:
        with PILImage.open(path) as image:
            width, height = image.size
    except OSError:
        return max_width, max_height
    if not width or not height:
        return max_width, max_height
    ratio = min(max_width / width, max_height / height)
    return width * ratio, height * ratio


def _title_from_caption(caption: str, fallback_stem: str) -> str:
    text = _stringify(caption)
    if not text:
        return fallback_stem.replace("_", " ").title()
    sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    words = sentence.split()
    if len(words) > 11:
        sentence = " ".join(words[:11]).rstrip(",;:") + "..."
    return sentence.rstrip(".")


def _data_description(workflow_state: dict[str, Any]) -> str:
    objective = workflow_state.get("workflow_objective", {}) or {}
    candidates = [
        workflow_state.get("data_description", ""),
        workflow_state.get("user_data_description", ""),
        objective.get("raw_description", "") if isinstance(objective, dict) else "",
        objective.get("decision_question", "") if isinstance(objective, dict) else "",
    ]
    for candidate in candidates:
        clean = _stringify(candidate)
        if clean:
            return clean
    return ""


def _objective_coverage(workflow_state: dict[str, Any]) -> list[str]:
    objective = workflow_state.get("workflow_objective", {}) or {}
    analysis_summary = workflow_state.get("analysis_results", {}).get("analysis_summary", {}) or {}
    decision = workflow_state.get("agent_outputs", {}).get("decision_maker", {}) or {}
    items: list[str] = []
    if isinstance(objective, dict):
        raw_description = _stringify(objective.get("raw_description", ""))
        decision_question = _stringify(objective.get("decision_question", ""))
        if raw_description:
            items.append(f"Objective: {raw_description}")
        elif decision_question:
            items.append(f"Objective: {decision_question}")
        limitations = objective.get("limitations", [])
        if limitations:
            items.append(f"Limitations: {'; '.join(_stringify(item) for item in limitations if _stringify(item))}")
    if isinstance(analysis_summary, dict):
        alignment = _stringify(analysis_summary.get("user_goal_alignment", ""))
        if alignment:
            items.append(f"Analysis alignment: {alignment}")
    recommendation = _stringify(decision.get("final_recommendation", ""))
    if recommendation:
        items.append(f"Decision answer: {recommendation}")
    if not items:
        items.append("No user objective was provided; the deck focuses on the most decision-useful findings.")
    return items


def _analysis_findings(analysis_results: dict[str, Any]) -> list[str]:
    summary = analysis_results.get("analysis_summary", {}) or {}
    findings = analysis_results.get("business_findings", []) or []
    items: list[str] = []
    if isinstance(summary, dict):
        for key, value in summary.items():
            if isinstance(value, (dict, list)):
                continue
            label = str(key).replace("_", " ").title()
            items.append(f"{label}: {_stringify(value)}")
    for finding in findings:
        if isinstance(finding, dict):
            statement = finding.get("finding") or finding.get("summary") or finding.get("insight")
            implication = finding.get("business_implication") or finding.get("implication")
            text = _stringify(statement)
            if implication:
                text = f"{text}. Implication: {_stringify(implication)}"
            if text:
                items.append(text)
        else:
            text = _stringify(finding)
            if text:
                items.append(text)
    return items[:5]


def _renumber(slides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    renumbered: list[dict[str, Any]] = []
    for index, slide in enumerate(slides, start=1):
        updated = dict(slide)
        updated["slide_number"] = index
        renumbered.append(updated)
    return renumbered


def _infer_layout(slide: dict[str, Any], index: int) -> str:
    title = _stringify(slide.get("title", "")).lower()
    has_visual = bool(_stringify(slide.get("visual_path", "") or slide.get("visual_element", "")))
    if has_visual:
        return "chart_with_takeaways" if slide.get("details") else "chart_focus"
    if index == 0 or "summary" in title:
        return "executive_summary"
    if "objective" in title or "dataset" in title or "context" in title:
        return "dataset_overview"
    if "recommend" in title or "decision" in title or "option" in title:
        return "recommendation_matrix"
    if "risk" in title or "limitation" in title or "next" in title:
        return "risk_limitations"
    if "closing" in title or "conclusion" in title or "final recommendation" in title or "final message" in title:
        return "closing"
    if "metric" in title or "kpi" in title:
        return "kpi_cards"
    if len(_as_list(slide.get("details"))) >= 3:
        return "insight_cards"
    return "two_column_insight"


def _normalize_details(value: Any) -> list[str]:
    details: list[str] = []
    for item in _as_list(value):
        if isinstance(item, dict):
            parts = []
            for key in ("action", "finding", "metric", "value", "rationale", "evidence", "impact", "timeline"):
                if item.get(key):
                    label = key.replace("_", " ").title()
                    parts.append(f"{label}: {_stringify(item.get(key))}")
            text = ". ".join(parts) if parts else _stringify(item)
        else:
            text = _stringify(item)
        if text:
            details.append(text)
    return details


def validate_slide(slide: dict[str, Any], saved_figures: list[str]) -> list[str]:
    issues: list[str] = []
    if slide.get("layout_type") not in ALLOWED_LAYOUT_TYPES:
        issues.append("invalid_layout_type")
    if len(_stringify(slide.get("title", ""))) > MAX_TITLE_CHARS:
        issues.append("title_too_long")
    if not _stringify(slide.get("main_message", "")):
        issues.append("missing_main_message")
    details = _normalize_details(slide.get("details", []))
    if len(details) > MAX_BULLETS:
        issues.append("too_many_bullets")
    if any(len(detail) > MAX_DETAIL_CHARS for detail in details):
        issues.append("details_too_verbose")
    visual_path = _stringify(slide.get("visual_path", ""))
    if visual_path and (visual_path not in saved_figures and not os.path.exists(visual_path)):
        issues.append("missing_visual_file")
    content_load = len(details) + (1 if _stringify(slide.get("business_implication", "")) else 0)
    if len(_stringify(slide.get("title", ""))) > 75 and len(_stringify(slide.get("main_message", ""))) > 130 and content_load >= 4:
        issues.append("overcrowded_slide_risk")
    return issues


def repair_slide(slide: dict[str, Any], issues: list[str], index: int) -> dict[str, Any]:
    repaired = dict(slide)
    if "invalid_layout_type" in issues:
        repaired["layout_type"] = _infer_layout(repaired, index)
    if "title_too_long" in issues:
        repaired["title"] = _shorten(repaired.get("title", ""), MAX_TITLE_CHARS)
    if "missing_main_message" in issues:
        details = _normalize_details(repaired.get("details", []))
        repaired["main_message"] = _shorten(details[0] if details else repaired.get("title", "Key message"), 155)
    details = _normalize_details(repaired.get("details", []))
    if "too_many_bullets" in issues:
        details = details[:MAX_BULLETS]
    if "details_too_verbose" in issues or "overcrowded_slide_risk" in issues:
        details = [_shorten(detail, MAX_DETAIL_CHARS) for detail in details]
    repaired["details"] = details[:MAX_BULLETS]
    if "missing_visual_file" in issues:
        repaired["visual_path"] = ""
        if repaired.get("layout_type") in VISUAL_LAYOUTS:
            repaired["layout_type"] = "two_column_insight" if repaired["details"] else "insight_cards"
    note = _stringify(repaired.get("speaker_note", ""))
    if issues:
        suffix = f"Slide validation: {', '.join(issues)}"
        repaired["speaker_note"] = f"{note} | {suffix}" if note else suffix
    return repaired


def _base_slide_from_legacy(slide: dict[str, Any], index: int, saved_figures: list[str]) -> tuple[dict[str, Any], list[str]]:
    visual_path = _stringify(slide.get("visual_path", "") or slide.get("visual_element", ""))
    normalized = {
        "slide_number": slide.get("slide_number", index + 1),
        "layout_type": slide.get("layout_type") or "",
        "title": _stringify(slide.get("title", f"Slide {index + 1}")),
        "main_message": _stringify(slide.get("main_message", "")),
        "details": _normalize_details(slide.get("details", [])),
        "visual_path": visual_path,
        "visual_type": _stringify(slide.get("visual_type", "")),
        "visual_caption": _stringify(slide.get("visual_caption", "")),
        "visual_takeaway": _stringify(slide.get("visual_takeaway", "")),
        "business_implication": _stringify(slide.get("business_implication", "")),
        "speaker_note": _stringify(slide.get("speaker_note", "")),
    }
    if not normalized["layout_type"]:
        normalized["layout_type"] = _infer_layout(normalized, index)
    issues = validate_slide(normalized, saved_figures)
    return repair_slide(normalized, issues, index), issues


def normalize_slide_plan(workflow_state: dict[str, Any]) -> list[dict[str, Any]]:
    outputs = workflow_state.get("agent_outputs", {}) or {}
    analysis_results = workflow_state.get("analysis_results", {}) or {}
    slide_plan = outputs.get("presentation_architect", {}) or {}
    raw_slides = slide_plan.get("slides", []) or []
    saved_figures = [figure for figure in workflow_state.get("saved_figures", []) if os.path.exists(figure)]
    warnings: list[dict[str, Any]] = []

    if not raw_slides:
        raw_slides = [
            {
                "slide_number": 1,
                "layout_type": "executive_summary",
                "title": "The analysis points to evidence-backed decisions",
                "main_message": outputs.get("decision_maker", {}).get("executive_summary", ""),
                "details": outputs.get("business_translator", {}).get("immediate_actions", [])[:4],
            },
            {
                "slide_number": 2,
                "layout_type": "recommendation_matrix",
                "title": "Recommended actions are tied to the strongest available evidence",
                "main_message": outputs.get("decision_maker", {}).get("final_recommendation", ""),
                "details": outputs.get("decision_maker", {}).get("recommendations", [])[:4],
            },
        ]

    objective_items = _objective_coverage(workflow_state)
    if not any("objective" in _stringify(slide.get("title", "")).lower() for slide in raw_slides):
        raw_slides.insert(
            0,
            {
                "slide_number": 0,
                "layout_type": "dataset_overview",
                "title": "Objective Coverage",
                "main_message": objective_items[0],
                "details": objective_items[1:5],
            },
        )

    analysis_items = _analysis_findings(analysis_results)
    if analysis_items:
        analysis_keywords = ("analysis", "finding", "insight", "visual")
        merged = False
        for slide in raw_slides:
            if any(keyword in _stringify(slide.get("title", "")).lower() for keyword in analysis_keywords):
                existing = _normalize_details(slide.get("details", []))
                slide["details"] = (existing + analysis_items[1:5])[:MAX_BULLETS]
                slide["main_message"] = slide.get("main_message") or analysis_items[0]
                merged = True
                break
        if not merged:
            insert_at = 1 if raw_slides else 0
            raw_slides.insert(
                insert_at,
                {
                    "slide_number": 0,
                    "layout_type": "insight_cards",
                    "title": "Technical Analysis Findings",
                    "main_message": analysis_items[0],
                    "details": analysis_items[1:5],
                },
            )

    normalized: list[dict[str, Any]] = []
    used_visuals: set[str] = set()
    for index, slide in enumerate(raw_slides):
        candidate, issues = _base_slide_from_legacy(dict(slide), index, saved_figures)
        visual_path = _stringify(candidate.get("visual_path", ""))
        if visual_path:
            if visual_path in used_visuals:
                candidate["visual_path"] = ""
                if candidate["layout_type"] in VISUAL_LAYOUTS:
                    candidate["layout_type"] = "two_column_insight"
                issues.append("duplicate_visual_removed")
            else:
                used_visuals.add(visual_path)
        if issues:
            warnings.append({"slide": candidate.get("title", f"Slide {index + 1}"), "issues": issues})
        normalized.append(candidate)

    figure_captions = analysis_results.get("figure_captions", {}) or {}
    next_index = len(normalized)
    for figure in saved_figures:
        if figure in used_visuals:
            continue
        caption = _stringify(figure_captions.get(figure, ""))
        title = _title_from_caption(caption, Path(figure).stem)
        normalized.append(
            {
                "slide_number": next_index + 1,
                "layout_type": "chart_focus",
                "title": title,
                "main_message": caption or f"This visual evidence supports the analysis of {Path(figure).stem.replace('_', ' ')}.",
                "details": [],
                "visual_path": figure,
                "visual_type": "analysis_figure",
                "visual_caption": "" if caption else Path(figure).name,
                "visual_takeaway": "",
                "business_implication": "",
                "speaker_note": "",
            }
        )
        used_visuals.add(figure)
        next_index += 1

    workflow_state["slide_validation_warnings"] = warnings
    return _renumber(normalized)


class DeckRenderer:
    def __init__(self, presentation: Any, theme: DeckTheme, deck_title: str, total_slides: int) -> None:
        self.prs = presentation
        self.theme = theme
        self.deck_title = deck_title
        self.total_slides = total_slides

    def _blank(self) -> Any:
        slide = self.prs.slides.add_slide(self.prs.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = _rgb(self.theme.sand)
        return slide

    def _rect(self, slide: Any, left: float, top: float, width: float, height: float, fill: tuple[int, int, int], radius: bool = False) -> Any:
        shape = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE,
            Inches(left),
            Inches(top),
            Inches(width),
            Inches(height),
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = _rgb(fill)
        shape.line.fill.background()
        return shape

    def _text(
        self,
        slide: Any,
        text: str,
        left: float,
        top: float,
        width: float,
        height: float,
        *,
        size: int = 12,
        color: tuple[int, int, int] | None = None,
        bold: bool = False,
        italic: bool = False,
        align: Any = PP_ALIGN.LEFT,
    ) -> Any:
        box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
        frame = box.text_frame
        frame.word_wrap = True
        frame.margin_left = Inches(0.08)
        frame.margin_right = Inches(0.08)
        frame.margin_top = Inches(0.04)
        frame.margin_bottom = Inches(0.04)
        paragraph = frame.paragraphs[0]
        paragraph.alignment = align
        run = paragraph.add_run()
        run.text = text
        run.font.name = self.theme.font_family
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.italic = italic
        run.font.color.rgb = _rgb(color or self.theme.charcoal)
        return box

    def _bullet_list(self, slide: Any, details: list[str], left: float, top: float, width: float, height: float, *, size: int = 12) -> None:
        if not details:
            return
        box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
        frame = box.text_frame
        frame.word_wrap = True
        frame.margin_left = Inches(0.08)
        frame.margin_right = Inches(0.08)
        frame.margin_top = Inches(0.04)
        frame.margin_bottom = Inches(0.04)
        for index, detail in enumerate(details[:MAX_BULLETS]):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            paragraph.space_after = Pt(7)
            bullet = paragraph.add_run()
            bullet.text = "\u25aa  "
            bullet.font.name = self.theme.font_family
            bullet.font.size = Pt(size)
            bullet.font.bold = True
            bullet.font.color.rgb = _rgb(self.theme.blue)
            body = paragraph.add_run()
            body.text = _shorten(detail, 180)
            body.font.name = self.theme.font_family
            body.font.size = Pt(size)
            body.font.color.rgb = _rgb(self.theme.charcoal)

    def _header(self, slide: Any, title: str) -> None:
        font_size = 24 if len(title) <= 52 else 21 if len(title) <= 75 else 18
        self._rect(slide, 0, 0, 0.36, self.theme.height, self.theme.navy)
        self._text(slide, title, 0.72, 0.35, 11.65, 0.78, size=font_size, color=self.theme.navy, bold=True)
        self._rect(slide, 0.8, 1.14, 1.05, 0.04, self.theme.gold)

    def _footer(self, slide: Any, slide_number: int) -> None:
        self._rect(slide, 0.78, 6.91, 11.85, 0.015, self.theme.slate)
        self._text(slide, _shorten(self.deck_title, 80), 0.78, 7.04, 8.6, 0.28, size=9, color=self.theme.slate)
        self._text(slide, f"{slide_number} / {self.total_slides}", 10.55, 7.04, 2.0, 0.28, size=9, color=self.theme.slate, align=PP_ALIGN.RIGHT)

    def _message_band(self, slide: Any, message: str, left: float, top: float, width: float, height: float = 0.76, color: tuple[int, int, int] | None = None) -> None:
        self._rect(slide, left, top, width, height, color or self.theme.blue, radius=True)
        self._text(slide, _shorten(message, 190), left + 0.15, top + 0.08, width - 0.3, height - 0.1, size=13, color=self.theme.white, bold=True)

    def _add_visual(self, slide: Any, visual_path: str, left: float, top: float, max_width: float, max_height: float) -> None:
        if not visual_path or not os.path.exists(visual_path):
            return
        width, height = _fit_image_size(visual_path, max_width, max_height)
        x = left + max((max_width - width) / 2, 0)
        y = top + max((max_height - height) / 2, 0)
        slide.shapes.add_picture(visual_path, Inches(x), Inches(y), Inches(width), Inches(height))

    def render_cover(self, deck_title: str, deck_subtitle: str) -> None:
        slide = self.prs.slides.add_slide(self.prs.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = _rgb(self.theme.navy)
        self._rect(slide, 9.7, 0, 3.63, self.theme.height, self.theme.blue)
        self._rect(slide, 9.7, 0, 0.06, self.theme.height, self.theme.gold)
        self._text(slide, "MULTI-AGENT ANALYTICS", 0.8, 0.55, 5.5, 0.35, size=10, color=self.theme.light_blue, bold=True)
        self._rect(slide, 0.8, 2.55, 1.4, 0.06, self.theme.gold)
        title_size = 34 if len(deck_title) <= 52 else 28
        self._text(slide, deck_title, 0.75, 2.82, 8.25, 1.75, size=title_size, color=self.theme.white, bold=True)
        self._text(slide, deck_subtitle, 0.78, 5.0, 8.1, 1.0, size=15, color=self.theme.light_blue)
        self._text(slide, datetime.now().strftime("%B %d, %Y").upper(), 0.78, 6.75, 4.0, 0.28, size=10, color=self.theme.light_blue)
        self._text(slide, "EXECUTIVE\nDECISION DECK", 10.1, 2.65, 2.7, 1.2, size=18, color=self.theme.white, bold=True, align=PP_ALIGN.CENTER)

    def render_executive_summary(self, slide_data: dict[str, Any], number: int) -> None:
        slide = self._blank()
        self._header(slide, slide_data["title"])
        self._message_band(slide, slide_data["main_message"], 0.78, 1.45, 11.65)
        details = slide_data.get("details", [])[:4]
        for index, detail in enumerate(details):
            col = index % 2
            row = index // 2
            left = 0.85 + col * 5.95
            top = 2.55 + row * 1.65
            self._rect(slide, left, top, 5.45, 1.22, self.theme.white, radius=True)
            self._rect(slide, left, top, 0.08, 1.22, self.theme.gold)
            self._text(slide, _shorten(detail, 150), left + 0.22, top + 0.18, 5.0, 0.82, size=12, color=self.theme.charcoal, bold=index == 0)
        self._footer(slide, number)

    def render_dataset_overview(self, slide_data: dict[str, Any], number: int) -> None:
        slide = self._blank()
        self._header(slide, slide_data["title"])
        self._message_band(slide, slide_data["main_message"], 0.78, 1.45, 7.2)
        implication = slide_data.get("business_implication") or "This frames which conclusions are supported by the available data."
        self._rect(slide, 8.35, 1.45, 4.05, 4.8, self.theme.white, radius=True)
        self._text(slide, "Why this matters", 8.62, 1.78, 3.5, 0.35, size=13, color=self.theme.navy, bold=True)
        self._text(slide, _shorten(implication, 260), 8.62, 2.22, 3.45, 2.0, size=12, color=self.theme.charcoal)
        self._bullet_list(slide, slide_data.get("details", []), 0.86, 2.55, 6.95, 3.8)
        self._footer(slide, number)

    def render_kpi_cards(self, slide_data: dict[str, Any], number: int) -> None:
        slide = self._blank()
        self._header(slide, slide_data["title"])
        self._message_band(slide, slide_data["main_message"], 0.78, 1.45, 11.65)
        for index, detail in enumerate(slide_data.get("details", [])[:4]):
            left = 0.9 + index * 3.0
            self._rect(slide, left, 2.65, 2.55, 2.4, self.theme.white, radius=True)
            pieces = str(detail).split(":", 1)
            value = pieces[1].strip() if len(pieces) == 2 else str(index + 1)
            label = pieces[0].strip() if len(pieces) == 2 else detail
            self._text(slide, _shorten(value, 24), left + 0.12, 3.0, 2.25, 0.62, size=23, color=self.theme.blue, bold=True, align=PP_ALIGN.CENTER)
            self._text(slide, _shorten(label, 58), left + 0.15, 3.75, 2.2, 0.9, size=11, color=self.theme.charcoal, align=PP_ALIGN.CENTER)
        self._footer(slide, number)

    def render_chart_focus(self, slide_data: dict[str, Any], number: int) -> None:
        slide = self._blank()
        self._header(slide, slide_data["title"])
        self._message_band(slide, slide_data["main_message"], 0.78, 1.35, 11.65, height=0.7)
        self._rect(slide, 0.95, 2.2, 11.25, 4.45, self.theme.white, radius=True)
        self._add_visual(slide, slide_data.get("visual_path", ""), 1.15, 2.35, 10.85, 4.1)
        takeaway = slide_data.get("visual_takeaway") or slide_data.get("business_implication") or ""
        if takeaway:
            self._text(slide, _shorten(takeaway, 120), 1.1, 6.42, 10.9, 0.28, size=10, color=self.theme.slate, italic=True, align=PP_ALIGN.CENTER)
        self._footer(slide, number)

    def render_chart_with_takeaways(self, slide_data: dict[str, Any], number: int) -> None:
        slide = self._blank()
        self._header(slide, slide_data["title"])
        self._message_band(slide, slide_data["main_message"], 0.78, 1.35, 11.65, height=0.7)
        self._rect(slide, 0.9, 2.25, 7.15, 4.2, self.theme.white, radius=True)
        self._add_visual(slide, slide_data.get("visual_path", ""), 1.08, 2.42, 6.78, 3.75)
        self._text(slide, _shorten(slide_data.get("visual_takeaway", ""), 120), 1.1, 6.18, 6.75, 0.28, size=9, color=self.theme.slate, italic=True)
        self._rect(slide, 8.35, 2.25, 4.05, 4.2, self.theme.white, radius=True)
        self._text(slide, "Takeaways", 8.62, 2.55, 3.5, 0.35, size=13, color=self.theme.navy, bold=True)
        self._bullet_list(slide, slide_data.get("details", []) or [slide_data.get("business_implication", "")], 8.58, 3.0, 3.55, 2.9, size=11)
        self._footer(slide, number)

    def render_two_column_insight(self, slide_data: dict[str, Any], number: int) -> None:
        slide = self._blank()
        self._header(slide, slide_data["title"])
        self._message_band(slide, slide_data["main_message"], 0.78, 1.45, 11.65)
        details = slide_data.get("details", [])
        split = max(1, min(len(details), 2))
        left_details = details[:split]
        right_details = details[split:] or [slide_data.get("business_implication", "")]
        self._rect(slide, 0.86, 2.55, 5.55, 3.75, self.theme.white, radius=True)
        self._rect(slide, 6.85, 2.55, 5.55, 3.75, self.theme.white, radius=True)
        self._text(slide, "Evidence", 1.1, 2.84, 4.8, 0.35, size=13, color=self.theme.navy, bold=True)
        self._text(slide, "Business meaning", 7.1, 2.84, 4.8, 0.35, size=13, color=self.theme.navy, bold=True)
        self._bullet_list(slide, left_details, 1.05, 3.35, 4.95, 2.3, size=11)
        self._bullet_list(slide, right_details, 7.05, 3.35, 4.95, 2.3, size=11)
        self._footer(slide, number)

    def render_insight_cards(self, slide_data: dict[str, Any], number: int) -> None:
        slide = self._blank()
        self._header(slide, slide_data["title"])
        self._message_band(slide, slide_data["main_message"], 0.78, 1.45, 11.65)
        details = slide_data.get("details", [])[:4]
        for index, detail in enumerate(details):
            left = 0.86 + (index % 2) * 6.0
            top = 2.48 + (index // 2) * 1.55
            self._rect(slide, left, top, 5.5, 1.15, self.theme.white, radius=True)
            self._rect(slide, left, top, 0.08, 1.15, self.theme.blue if index % 2 == 0 else self.theme.gold)
            self._text(slide, _shorten(detail, 145), left + 0.22, top + 0.16, 5.0, 0.76, size=11, color=self.theme.charcoal)
        self._footer(slide, number)

    def render_recommendation_matrix(self, slide_data: dict[str, Any], number: int) -> None:
        slide = self._blank()
        self._header(slide, slide_data["title"])
        self._message_band(slide, slide_data["main_message"], 0.78, 1.32, 11.65, height=0.7, color=self.theme.green)
        headers = ["Action", "Why it matters", "Evidence / impact"]
        widths = [3.55, 4.05, 3.85]
        lefts = [0.86, 4.45, 8.55]
        for header, left, width in zip(headers, lefts, widths):
            self._rect(slide, left, 2.25, width, 0.42, self.theme.navy)
            self._text(slide, header, left + 0.08, 2.31, width - 0.16, 0.25, size=10, color=self.theme.white, bold=True, align=PP_ALIGN.CENTER)
        for row, detail in enumerate(slide_data.get("details", [])[:3]):
            top = 2.85 + row * 1.1
            for left, width in zip(lefts, widths):
                self._rect(slide, left, top, width, 0.9, self.theme.white, radius=True)
            text = _stringify(detail)
            parts = re.split(r"\.\s+", text)
            self._text(slide, _shorten(parts[0] if parts else text, 95), lefts[0] + 0.08, top + 0.12, widths[0] - 0.16, 0.55, size=10, color=self.theme.charcoal)
            self._text(slide, _shorten(parts[1] if len(parts) > 1 else text, 105), lefts[1] + 0.08, top + 0.12, widths[1] - 0.16, 0.55, size=10, color=self.theme.charcoal)
            self._text(slide, _shorten(" ".join(parts[2:]) if len(parts) > 2 else slide_data.get("business_implication", ""), 105), lefts[2] + 0.08, top + 0.12, widths[2] - 0.16, 0.55, size=10, color=self.theme.charcoal)
        self._footer(slide, number)

    def render_risk_limitations(self, slide_data: dict[str, Any], number: int) -> None:
        slide = self._blank()
        self._header(slide, slide_data["title"])
        self._message_band(slide, slide_data["main_message"], 0.78, 1.45, 11.65, color=self.theme.red)
        self._rect(slide, 0.86, 2.55, 5.55, 3.75, self.theme.white, radius=True)
        self._rect(slide, 6.85, 2.55, 5.55, 3.75, self.theme.white, radius=True)
        self._text(slide, "Known limits", 1.1, 2.84, 4.8, 0.35, size=13, color=self.theme.red, bold=True)
        self._text(slide, "Next checks", 7.1, 2.84, 4.8, 0.35, size=13, color=self.theme.navy, bold=True)
        details = slide_data.get("details", [])
        split = (len(details) + 1) // 2
        self._bullet_list(slide, details[:split], 1.05, 3.35, 4.95, 2.4, size=11)
        self._bullet_list(slide, details[split:] or [slide_data.get("business_implication", "")], 7.05, 3.35, 4.95, 2.4, size=11)
        self._footer(slide, number)

    def render_closing(self, slide_data: dict[str, Any], number: int) -> None:
        slide = self.prs.slides.add_slide(self.prs.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = _rgb(self.theme.navy)
        self._rect(slide, 0, 0, 0.42, self.theme.height, self.theme.gold)
        self._text(slide, slide_data["title"], 0.85, 1.35, 11.1, 0.9, size=34, color=self.theme.white, bold=True)
        self._rect(slide, 0.88, 2.38, 11.6, 0.04, self.theme.gold)
        self._text(slide, _shorten(slide_data["main_message"], 230), 0.85, 2.75, 10.9, 1.35, size=18, color=self.theme.light_blue)
        self._bullet_list(slide, slide_data.get("details", []), 0.9, 4.45, 10.4, 1.5, size=12)
        self._text(slide, "Generated by the Multi-Agent Analytics System", 0.85, 6.85, 7.5, 0.3, size=10, color=self.theme.light_blue)
        self._text(slide, f"{number} / {self.total_slides}", 10.55, 6.85, 2.0, 0.3, size=10, color=self.theme.light_blue, align=PP_ALIGN.RIGHT)

    def render_slide(self, slide_data: dict[str, Any], number: int) -> None:
        renderers: dict[str, Callable[[dict[str, Any], int], None]] = {
            "executive_summary": self.render_executive_summary,
            "dataset_overview": self.render_dataset_overview,
            "kpi_cards": self.render_kpi_cards,
            "chart_focus": self.render_chart_focus,
            "chart_with_takeaways": self.render_chart_with_takeaways,
            "two_column_insight": self.render_two_column_insight,
            "insight_cards": self.render_insight_cards,
            "recommendation_matrix": self.render_recommendation_matrix,
            "risk_limitations": self.render_risk_limitations,
            "closing": self.render_closing,
        }
        renderer = renderers.get(slide_data.get("layout_type"), self.render_two_column_insight)
        renderer(slide_data, number)


def build_consulting_deck(workflow_state: dict[str, Any], output_path: str = "analytics_report.pptx") -> str:
    outputs = workflow_state.get("agent_outputs", {}) or {}
    slide_plan = outputs.get("presentation_architect", {}) or {}
    data_description = _data_description(workflow_state)
    slides = normalize_slide_plan(workflow_state)
    resolved_output_path = _preferred_output_path(output_path)

    deck_title = _stringify(slide_plan.get("presentation_title", "")) or "Analytics Executive Brief"
    deck_subtitle = _stringify(slide_plan.get("presentation_subtitle", "")) or "Decision-ready analytics deck"
    if data_description and deck_subtitle == "Decision-ready analytics deck":
        deck_subtitle = _shorten(data_description, 100)

    if PPTX_AVAILABLE:
        presentation = Presentation()
        presentation.slide_width = Inches(THEME.width)
        presentation.slide_height = Inches(THEME.height)
        total_slides = len(slides) + 1
        renderer = DeckRenderer(presentation, THEME, deck_title, total_slides)
        renderer.render_cover(deck_title, deck_subtitle)
        for offset, slide in enumerate(slides, start=2):
            renderer.render_slide(slide, offset)
        presentation.save(resolved_output_path)
        return resolved_output_path

    resolved_pptx_path = Path(resolved_output_path)
    fallback_path = _preferred_output_path(
        str(resolved_pptx_path.with_name(f"{resolved_pptx_path.stem}_slide_deck_fallback.txt"))
    )
    lines = [deck_title, deck_subtitle, ""]
    if data_description:
        lines.extend(["Data description", textwrap.fill(data_description, width=100), ""])
    for slide in slides:
        lines.append(f"Slide {slide.get('slide_number', '?')}: {slide.get('title', 'Untitled')}")
        lines.append(f"Layout: {slide.get('layout_type', 'unknown')}")
        lines.append(f"Message: {slide.get('main_message', '')}")
        for detail in slide.get("details", []):
            lines.append(f"  - {textwrap.fill(str(detail), width=100)}")
        if slide.get("business_implication"):
            lines.append(f"Business implication: {slide.get('business_implication')}")
        if slide.get("visual_path"):
            lines.append(f"Visual: {slide.get('visual_path')}")
        lines.append("")
    Path(fallback_path).write_text("\n".join(lines), encoding="utf-8")
    return fallback_path
