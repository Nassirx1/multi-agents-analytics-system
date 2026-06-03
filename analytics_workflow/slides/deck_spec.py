from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .templates import template_for_legacy_layout
from .text_refiner import compact_whitespace, refine_bullets, refine_headline, shorten


@dataclass
class ContentBlock:
    type: str
    items: list[str] = field(default_factory=list)
    title: str = ""
    text: str = ""

    @classmethod
    def from_any(cls, value: Any) -> "ContentBlock":
        if isinstance(value, ContentBlock):
            return value
        if isinstance(value, dict):
            return cls(
                type=compact_whitespace(value.get("type", "bullets")) or "bullets",
                items=refine_bullets(value.get("items", []), max_items=5),
                title=shorten(value.get("title", ""), 48),
                text=shorten(value.get("text", ""), 180),
            )
        return cls(type="bullets", items=refine_bullets(value, max_items=5))

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "title": self.title, "text": self.text, "items": list(self.items)}


@dataclass
class VisualSpec:
    type: str = ""
    chart_type: str = ""
    artifact_id: str = ""
    chart_spec_id: str = ""
    title: str = ""
    takeaway: str = ""
    finding: str = ""
    data_reference: str = ""
    image_path: str = ""
    x: str = ""
    y: str = ""
    group_by: str = ""
    x_label: str = ""
    y_label: str = ""
    value_format: str = ""
    render_mode: str = ""
    series: Any = ""
    data: Any = None
    fallback_path: str = ""
    fallback_reason: str = ""
    recommended_template: str = ""
    slide_candidate: bool = False

    @classmethod
    def from_any(cls, value: Any) -> "VisualSpec | None":
        if not value:
            return None
        if isinstance(value, VisualSpec):
            return value
        if isinstance(value, str):
            clean = compact_whitespace(value)
            return cls(type="image", image_path=clean) if clean else None
        if not isinstance(value, dict):
            return None
        visual_type = compact_whitespace(value.get("type", ""))
        image_path = compact_whitespace(
            value.get("image_path") or value.get("visual_path") or value.get("visual_element") or value.get("fallback_path")
        )
        chart_type = compact_whitespace(value.get("chart_type") or value.get("visual_type"))
        if not visual_type:
            artifact_type = compact_whitespace(value.get("artifact_type"))
            visual_type = "structured_chart" if artifact_type == "chart_spec" or chart_type and (value.get("data") or value.get("series")) else "image" if image_path else ""
        artifact_id = compact_whitespace(value.get("artifact_id") or value.get("id") or value.get("chart_spec_id"))
        series_value = value.get("series") if "series" in value else value.get("group_by")
        normalized_series = series_value if isinstance(series_value, list) else compact_whitespace(series_value)
        return cls(
            type=visual_type,
            chart_type=chart_type,
            artifact_id=artifact_id,
            chart_spec_id=artifact_id,
            title=shorten(value.get("title", ""), 90),
            takeaway=shorten(value.get("takeaway") or value.get("visual_takeaway"), 150),
            finding=shorten(value.get("finding", ""), 180),
            data_reference=compact_whitespace(value.get("data_reference")),
            image_path=image_path,
            x=compact_whitespace(value.get("x")),
            y=compact_whitespace(value.get("y")),
            group_by=compact_whitespace(value.get("group_by")),
            x_label=compact_whitespace(value.get("x_label")),
            y_label=compact_whitespace(value.get("y_label")),
            value_format=compact_whitespace(value.get("value_format")),
            render_mode=compact_whitespace(value.get("render_mode")),
            series=normalized_series,
            data=value.get("data") or value.get("rows") or value.get("values"),
            fallback_path=compact_whitespace(value.get("fallback_path")),
            fallback_reason=compact_whitespace(value.get("fallback_reason")),
            recommended_template=compact_whitespace(value.get("recommended_template")),
            slide_candidate=bool(value.get("slide_candidate", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "chart_type": self.chart_type,
            "artifact_id": self.artifact_id,
            "chart_spec_id": self.chart_spec_id,
            "title": self.title,
            "takeaway": self.takeaway,
            "finding": self.finding,
            "data_reference": self.data_reference,
            "image_path": self.image_path,
            "x": self.x,
            "y": self.y,
            "group_by": self.group_by,
            "x_label": self.x_label,
            "y_label": self.y_label,
            "value_format": self.value_format,
            "render_mode": self.render_mode,
            "series": self.series,
            "data": self.data,
            "fallback_path": self.fallback_path,
            "fallback_reason": self.fallback_reason,
            "recommended_template": self.recommended_template,
            "slide_candidate": self.slide_candidate,
        }


@dataclass
class SlideSpec:
    slide_number: int
    slide_role: str
    template: str
    headline: str
    main_message: str = ""
    subtitle: str = ""
    content_blocks: list[ContentBlock] = field(default_factory=list)
    visual: VisualSpec | None = None
    metrics: list[dict[str, str]] = field(default_factory=list)
    speaker_note: str = ""

    @classmethod
    def from_legacy(cls, slide: dict[str, Any], index: int) -> "SlideSpec":
        template = compact_whitespace(slide.get("template"))
        if not template:
            template = template_for_legacy_layout(compact_whitespace(slide.get("layout_type")))
        visual = VisualSpec.from_any(
            slide.get("visual")
            or {
                "type": "image",
                "image_path": slide.get("visual_path") or slide.get("visual_element"),
                "chart_type": slide.get("visual_type", ""),
                "takeaway": slide.get("visual_takeaway", ""),
            }
        )
        details = refine_bullets(slide.get("details", []), max_items=4)
        return cls(
            slide_number=int(slide.get("slide_number") or index),
            slide_role=compact_whitespace(slide.get("slide_role") or "analysis"),
            template=template,
            headline=refine_headline(slide.get("headline") or slide.get("title"), f"Finding {index}"),
            main_message=shorten(slide.get("main_message", ""), 170),
            subtitle=shorten(slide.get("subtitle", ""), 120),
            content_blocks=[ContentBlock(type="bullets", items=details)] if details else [],
            visual=visual,
            speaker_note=shorten(slide.get("speaker_note", ""), 220),
        )

    @property
    def bullets(self) -> list[str]:
        for block in self.content_blocks:
            if block.items:
                return block.items
        return []

    def to_dict(self) -> dict[str, Any]:
        return {
            "slide_number": self.slide_number,
            "slide_role": self.slide_role,
            "template": self.template,
            "headline": self.headline,
            "main_message": self.main_message,
            "subtitle": self.subtitle,
            "content_blocks": [block.to_dict() for block in self.content_blocks],
            "visual": self.visual.to_dict() if self.visual else None,
            "metrics": [dict(metric) for metric in self.metrics],
            "speaker_note": self.speaker_note,
        }

    def to_legacy_dict(self) -> dict[str, Any]:
        visual_path = self.visual.image_path if self.visual else ""
        return {
            "slide_number": self.slide_number,
            "layout_type": self.template,
            "title": self.headline,
            "main_message": self.main_message,
            "details": self.bullets,
            "visual_path": visual_path,
            "visual_element": visual_path,
            "visual_type": self.visual.chart_type if self.visual else "",
            "visual_takeaway": self.visual.takeaway if self.visual else "",
            "speaker_note": self.speaker_note,
        }


@dataclass
class DeckSpec:
    deck_title: str
    audience: str = "executive"
    theme: str = "consulting_minimal"
    subtitle: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    dataset_context: dict[str, Any] = field(default_factory=dict)
    slides: list[SlideSpec] = field(default_factory=list)

    def renumber(self) -> "DeckSpec":
        for index, slide in enumerate(self.slides, start=1):
            slide.slide_number = index
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "deck_title": self.deck_title,
            "audience": self.audience,
            "theme": self.theme,
            "subtitle": self.subtitle,
            "metadata": dict(self.metadata),
            "dataset_context": dict(self.dataset_context),
            "slides": [slide.to_dict() for slide in self.slides],
        }
