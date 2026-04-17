from __future__ import annotations

import html
import json
import os
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from reportlab.lib import colors as rl_colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

try:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches, Pt

    PPTX_AVAILABLE = True
except ImportError:
    PPTX_AVAILABLE = False


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, indent=2, default=str)


def _safe_paragraph(text: str) -> str:
    return html.escape(text).replace("\n", "<br/>")


def _add_pdf_heading(story: list[Any], text: str, style: Any) -> None:
    story.append(Paragraph(_safe_paragraph(text), style))
    story.append(Spacer(1, 0.08 * inch))


def _add_pdf_body(story: list[Any], text: str, style: Any) -> None:
    clean = _stringify(text)
    if clean:
        story.append(Paragraph(_safe_paragraph(clean), style))
        story.append(Spacer(1, 0.1 * inch))


def _add_pdf_bullets(story: list[Any], items: list[str], style: Any) -> None:
    for item in items:
        clean = _stringify(item)
        if clean:
            story.append(Paragraph(_safe_paragraph(f"- {clean}"), style))
            story.append(Spacer(1, 0.05 * inch))


def _format_dataset_overview(data_understander: dict[str, Any]) -> str:
    datasets = data_understander.get("datasets", {})
    parts = []
    for dataset_name, dataset_info in datasets.items():
        summary = dataset_info.get("quality_summary", "")
        analyses = ", ".join(dataset_info.get("recommended_analyses", [])[:3])
        segment = f"{dataset_name}: {summary}"
        if analyses:
            segment += f" Recommended analyses: {analyses}."
        parts.append(segment)
    return "\n".join(parts) or data_understander.get("executive_summary", "")


def _source_index_map(market_researcher: dict[str, Any]) -> dict[int, dict[str, Any]]:
    mapping: dict[int, dict[str, Any]] = {}
    for fallback_index, source in enumerate(market_researcher.get("sources_cited", []), start=1):
        try:
            index = int(source.get("index", fallback_index))
        except (TypeError, ValueError):
            index = fallback_index
        mapping[index] = source
    return mapping


def _format_market_findings(market_researcher: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    overview = market_researcher.get("industry_overview", "")
    if overview:
        findings.append(overview)

    sources = _source_index_map(market_researcher)
    market_findings = market_researcher.get("market_findings", [])
    if market_findings:
        for finding in market_findings[:5]:
            claim = finding.get("claim", "")
            try:
                source_index = int(finding.get("source_index", 1))
            except (TypeError, ValueError):
                source_index = 1
            findings.append(f"{claim} [{source_index}]")
            source = sources.get(source_index)
            if source:
                findings.append(
                    f"Source [{source_index}]: {source.get('title', '')} - {source.get('url', '')}"
                )
        return findings

    for index, trend in enumerate(market_researcher.get("key_trends", [])[:4], start=1):
        findings.append(f"{trend} [{index}]")
        source = sources.get(index)
        if source:
            findings.append(f"Source [{index}]: {source.get('title', '')} - {source.get('url', '')}")

    for index, opportunity in enumerate(market_researcher.get("opportunities", [])[:3], start=1):
        findings.append(f"{opportunity} [{index}]")
        source = sources.get(index)
        if source:
            findings.append(f"Source [{index}]: {source.get('title', '')} - {source.get('url', '')}")

    return findings


def _market_claim_pairs(market_researcher: dict[str, Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    sources = _source_index_map(market_researcher)
    market_findings = market_researcher.get("market_findings", [])
    if market_findings:
        for finding in market_findings[:5]:
            claim = finding.get("claim", "")
            try:
                source_index = int(finding.get("source_index", 1))
            except (TypeError, ValueError):
                source_index = 1
            source = sources.get(source_index, {})
            source_bits = [source.get("title", "").strip(), source.get("url", "").strip()]
            source_text = (
                f"Source [{source_index}]: " + " - ".join(bit for bit in source_bits if bit)
                if any(source_bits)
                else ""
            )
            pairs.append((f"{claim} [{source_index}]", source_text))
        return pairs

    fallback_items = market_researcher.get("key_trends", [])[:4] + market_researcher.get("opportunities", [])[:3]
    for index, item in enumerate(fallback_items, start=1):
        source = sources.get(index, {})
        source_bits = [source.get("title", "").strip(), source.get("url", "").strip()]
        source_text = (
            f"Source [{index}]: " + " - ".join(bit for bit in source_bits if bit)
            if any(source_bits)
            else ""
        )
        pairs.append((f"{item} [{index}]", source_text))
    return pairs


def _format_analysis_findings(analysis_results: dict[str, Any]) -> list[str]:
    findings = []
    findings.extend(analysis_results.get("business_findings", [])[:6])
    summary = analysis_results.get("analysis_summary", {})
    if isinstance(summary, dict):
        for key, value in list(summary.items())[:6]:
            findings.append(f"{str(key).replace('_', ' ').title()}: {_stringify(value)}")
    return findings


def _format_recommendations(recommendations: list[dict[str, Any]]) -> list[str]:
    formatted = []
    for item in recommendations[:5]:
        action = item.get("action", "")
        rationale = item.get("rationale", "")
        evidence = item.get("evidence", "")
        timeline = item.get("timeline", "")
        impact = item.get("impact", "")
        text = f"{action}. Why: {rationale}."
        if evidence:
            text += f" Evidence: {evidence}."
        if timeline or impact:
            text += f" Timeline: {timeline or 'TBD'}. Impact: {impact or 'TBD'}."
        formatted.append(text)
    return formatted


def _expand_slides_with_visuals(
    slides: list[dict[str, Any]],
    saved_figures: list[str],
    figure_captions: dict[str, str],
) -> list[dict[str, Any]]:
    expanded = [dict(slide) for slide in slides]
    used_visuals = {
        slide.get("visual_element", "")
        for slide in expanded
        if slide.get("visual_element") and os.path.exists(slide.get("visual_element", ""))
    }
    next_number = max([slide.get("slide_number", 0) for slide in expanded], default=0) + 1

    for figure in saved_figures:
        if not os.path.exists(figure) or figure in used_visuals:
            continue
        caption = figure_captions.get(figure, "").strip()
        expanded.append(
            {
                "slide_number": next_number,
                "title": f"Visual Insight {next_number - 1}",
                "main_message": caption or f"Evidence from {Path(figure).stem.replace('_', ' ')}",
                "details": [caption or "This visual was generated by the analysis workflow and should be reviewed with the numeric findings."],
                "visual_element": figure,
            }
        )
        next_number += 1

    return expanded


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


def generate_pdf_report(workflow_state: dict[str, Any], output_path: str = "analytics_report.pdf") -> str:
    outputs = workflow_state.get("agent_outputs", {})
    analysis_results = workflow_state.get("analysis_results", {})
    resolved_output_path = _preferred_output_path(output_path)
    if REPORTLAB_AVAILABLE:
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(name="ReportTitle", parent=styles["Title"], textColor=rl_colors.HexColor("#16324F")))
        styles.add(ParagraphStyle(name="ReportHeading", parent=styles["Heading2"], textColor=rl_colors.HexColor("#16324F"), spaceBefore=12))
        styles.add(ParagraphStyle(name="ReportBody", parent=styles["BodyText"], leading=15, spaceAfter=6))
        styles.add(ParagraphStyle(name="ReportCaption", parent=styles["BodyText"], textColor=rl_colors.HexColor("#5B6570"), fontSize=9, italic=True))

        story: list[Any] = [Paragraph("Multi-Agent Analytics Report", styles["ReportTitle"]), Spacer(1, 0.15 * inch)]
        story.append(Paragraph(datetime.now().strftime("%B %d, %Y"), styles["Normal"]))
        story.append(Spacer(1, 0.2 * inch))

        _add_pdf_heading(story, "Executive Summary", styles["ReportHeading"])
        _add_pdf_body(story, outputs.get("decision_maker", {}).get("executive_summary", ""), styles["ReportBody"])
        _add_pdf_body(story, outputs.get("decision_maker", {}).get("decision_context", ""), styles["ReportBody"])

        _add_pdf_heading(story, "Dataset Overview / Data Understanding", styles["ReportHeading"])
        _add_pdf_body(story, outputs.get("data_understander", {}).get("executive_summary", ""), styles["ReportBody"])
        _add_pdf_body(story, _format_dataset_overview(outputs.get("data_understander", {})), styles["ReportBody"])

        _add_pdf_heading(story, "Market Research", styles["ReportHeading"])
        market_research = outputs.get("market_researcher", {})
        _add_pdf_body(story, market_research.get("industry_overview", ""), styles["ReportBody"])
        for claim, source_text in _market_claim_pairs(market_research):
            story.append(Paragraph(_safe_paragraph(f"- {claim}"), styles["ReportBody"]))
            if source_text:
                story.append(Paragraph(_safe_paragraph(source_text), styles["ReportCaption"]))
            story.append(Spacer(1, 0.05 * inch))

        _add_pdf_heading(story, "Analysis Plan", styles["ReportHeading"])
        _add_pdf_bullets(story, outputs.get("planner", {}).get("objectives", []), styles["ReportBody"])
        _add_pdf_bullets(story, outputs.get("planner", {}).get("statistical_methods", []), styles["ReportBody"])

        _add_pdf_heading(story, "Data Analysis and Visual Findings", styles["ReportHeading"])
        _add_pdf_bullets(story, _format_analysis_findings(analysis_results), styles["ReportBody"])
        for figure in workflow_state.get("saved_figures", [])[:4]:
            caption = analysis_results.get("figure_captions", {}).get(figure, "")
            if os.path.exists(figure):
                from reportlab.platypus import Image as RLImage

                story.append(RLImage(figure, width=6.2 * inch, height=3.6 * inch))
                if caption:
                    story.append(Paragraph(_safe_paragraph(f"Figure note: {caption}"), styles["ReportCaption"]))
                story.append(Spacer(1, 0.12 * inch))

        _add_pdf_heading(story, "Business Translation", styles["ReportHeading"])
        _add_pdf_body(story, outputs.get("business_translator", {}).get("executive_summary", ""), styles["ReportBody"])
        _add_pdf_body(story, outputs.get("business_translator", {}).get("business_narrative", ""), styles["ReportBody"])
        _add_pdf_bullets(story, outputs.get("business_translator", {}).get("opportunities", []), styles["ReportBody"])
        _add_pdf_bullets(story, outputs.get("business_translator", {}).get("risks", []), styles["ReportBody"])

        _add_pdf_heading(story, "Decision Recommendations", styles["ReportHeading"])
        _add_pdf_body(story, outputs.get("decision_maker", {}).get("final_recommendation", ""), styles["ReportBody"])
        _add_pdf_bullets(story, _format_recommendations(outputs.get("decision_maker", {}).get("recommendations", [])), styles["ReportBody"])

        _add_pdf_heading(story, "Appendix / Sources", styles["ReportHeading"])
        appendix_items = []
        for index, source in enumerate(outputs.get("market_researcher", {}).get("sources_cited", []), start=1):
            appendix_items.append(f"[{index}] {source.get('title', '')} - {source.get('url', '')}")
        if not appendix_items:
            appendix_items = ["No external sources were captured for this run."]
        _add_pdf_bullets(story, appendix_items, styles["ReportBody"])

        doc = SimpleDocTemplate(resolved_output_path, pagesize=A4, rightMargin=0.7 * inch, leftMargin=0.7 * inch)
        doc.build(story)
        return resolved_output_path

    resolved_pdf_path = Path(resolved_output_path)
    fallback_path = _preferred_output_path(
        str(resolved_pdf_path.with_name(f"{resolved_pdf_path.stem}_pdf_fallback.txt"))
    )
    lines = [
        "Multi-Agent Analytics Report",
        datetime.now().strftime("%B %d, %Y"),
        "",
        "Executive Summary",
        _stringify(outputs.get("decision_maker", {}).get("executive_summary", "")),
        "",
        "Decision Context",
        _stringify(outputs.get("decision_maker", {}).get("decision_context", "")),
        "",
        "Dataset Overview",
        _format_dataset_overview(outputs.get("data_understander", {})),
        "",
        "Market Research",
        "\n".join(
            [line for pair in _market_claim_pairs(outputs.get("market_researcher", {})) for line in pair if line]
        ),
        "",
        "Data Analysis and Visual Findings",
        "\n".join(_format_analysis_findings(analysis_results)),
        "",
        "Business Translation",
        _stringify(outputs.get("business_translator", {}).get("business_narrative", "")),
        "",
        "Recommendations",
        "\n".join(_format_recommendations(outputs.get("decision_maker", {}).get("recommendations", []))),
    ]
    Path(fallback_path).write_text("\n".join(lines), encoding="utf-8")
    return fallback_path


def generate_slide_deck(workflow_state: dict[str, Any], output_path: str = "analytics_report.pptx") -> str:
    outputs = workflow_state.get("agent_outputs", {})
    analysis_results = workflow_state.get("analysis_results", {})
    resolved_output_path = _preferred_output_path(output_path)
    slide_plan = outputs.get("presentation_architect", {})
    slides = slide_plan.get("slides", [])
    if not slides:
        slides = [
            {
                "slide_number": 1,
                "title": "Executive Summary",
                "main_message": outputs.get("decision_maker", {}).get("executive_summary", ""),
                "details": outputs.get("business_translator", {}).get("immediate_actions", [])[:4],
                "visual_element": "",
            },
            {
                "slide_number": 2,
                "title": "Recommendations",
                "main_message": outputs.get("decision_maker", {}).get("final_recommendation", ""),
                "details": [item.get("action", "") for item in outputs.get("decision_maker", {}).get("recommendations", [])[:5]],
                "visual_element": workflow_state.get("saved_figures", [""])[0] if workflow_state.get("saved_figures") else "",
            },
        ]
    slides = _expand_slides_with_visuals(
        slides,
        workflow_state.get("saved_figures", []),
        analysis_results.get("figure_captions", {}),
    )

    if PPTX_AVAILABLE:
        presentation = Presentation()
        presentation.slide_width = Inches(13.33)
        presentation.slide_height = Inches(7.5)
        navy = RGBColor(0x12, 0x2B, 0x45)
        blue = RGBColor(0x1F, 0x5E, 0xA8)
        sand = RGBColor(0xF3, 0xF0, 0xE8)
        white = RGBColor(0xFF, 0xFF, 0xFF)
        charcoal = RGBColor(0x24, 0x2E, 0x38)
        gold = RGBColor(0xC8, 0x9B, 0x3C)

        cover = presentation.slides.add_slide(presentation.slide_layouts[6])
        cover.background.fill.solid()
        cover.background.fill.fore_color.rgb = navy
        cover_panel = cover.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(9.9), Inches(0), Inches(3.43), Inches(7.5))
        cover_panel.fill.solid()
        cover_panel.fill.fore_color.rgb = blue
        cover_panel.line.fill.background()
        title_box = cover.shapes.add_textbox(Inches(0.8), Inches(1.2), Inches(8.5), Inches(1.8))
        title_tf = title_box.text_frame
        title_tf.word_wrap = True
        p = title_tf.paragraphs[0]
        r = p.add_run()
        r.text = slide_plan.get("presentation_title", "Analytics Report")
        r.font.size = Pt(28 if len(slide_plan.get("presentation_title", "Analytics Report")) <= 40 else 24)
        r.font.bold = True
        r.font.color.rgb = white
        sub_box = cover.shapes.add_textbox(Inches(0.8), Inches(3.2), Inches(8.3), Inches(1.0))
        p = sub_box.text_frame.paragraphs[0]
        r = p.add_run()
        r.text = slide_plan.get("presentation_subtitle", "Decision-ready analytics brief")
        r.font.size = Pt(15)
        r.font.color.rgb = white
        date_box = cover.shapes.add_textbox(Inches(0.8), Inches(6.6), Inches(3), Inches(0.4))
        p = date_box.text_frame.paragraphs[0]
        r = p.add_run()
        r.text = datetime.now().strftime("%B %d, %Y")
        r.font.size = Pt(10)
        r.font.color.rgb = white

        total_slides = len(slides) + 1
        for offset, slide in enumerate(slides, start=2):
            current = presentation.slides.add_slide(presentation.slide_layouts[6])
            current.background.fill.solid()
            current.background.fill.fore_color.rgb = sand
            title_text = slide.get("title", f"Slide {offset - 1}")
            main_message = slide.get("main_message", "")
            details = slide.get("details", [])[:5]
            visual = slide.get("visual_element", "")
            has_visual = bool(visual and os.path.exists(visual))
            title_font_size = 24 if len(title_text) <= 40 else 21 if len(title_text) <= 65 else 18
            message_font_size = 14 if len(main_message) <= 80 else 12 if len(main_message) <= 140 else 11
            detail_font_size = 13
            if any(len(str(detail)) > 110 for detail in details):
                detail_font_size = 11
            detail_width = Inches(5.9) if has_visual else Inches(11.4)

            band = current.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(0.45), Inches(7.5))
            band.fill.solid()
            band.fill.fore_color.rgb = navy
            band.line.fill.background()

            header = current.shapes.add_textbox(Inches(0.8), Inches(0.45), Inches(9.8), Inches(0.9))
            header.text_frame.word_wrap = True
            p = header.text_frame.paragraphs[0]
            r = p.add_run()
            r.text = title_text
            r.font.size = Pt(title_font_size)
            r.font.bold = True
            r.font.color.rgb = navy

            msg_shape = current.shapes.add_shape(
                MSO_SHAPE.ROUNDED_RECTANGLE,
                Inches(0.8),
                Inches(1.3),
                Inches(6.1) if has_visual else Inches(11.4),
                Inches(1.0 if len(main_message) <= 90 else 1.2),
            )
            msg_shape.fill.solid()
            msg_shape.fill.fore_color.rgb = blue
            msg_shape.line.fill.background()
            msg_box = current.shapes.add_textbox(
                Inches(1.0),
                Inches(1.45),
                Inches(5.7) if has_visual else Inches(11.0),
                Inches(0.7),
            )
            msg_box.text_frame.word_wrap = True
            p = msg_box.text_frame.paragraphs[0]
            r = p.add_run()
            r.text = main_message
            r.font.size = Pt(message_font_size)
            r.font.bold = True
            r.font.color.rgb = white

            details_box = current.shapes.add_textbox(Inches(0.9), Inches(2.6), detail_width, Inches(3.7))
            details_tf = details_box.text_frame
            details_tf.word_wrap = True
            for idx, detail in enumerate(details):
                paragraph = details_tf.paragraphs[0] if idx == 0 else details_tf.add_paragraph()
                paragraph.level = 0
                paragraph.space_after = Pt(10)
                run = paragraph.add_run()
                run.text = textwrap.shorten(str(detail), width=180, placeholder="...")
                run.font.size = Pt(detail_font_size)
                run.font.color.rgb = charcoal

            if has_visual:
                current.shapes.add_picture(visual, Inches(7.2), Inches(1.55), Inches(5.3), Inches(3.8))

            foot = current.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.8), Inches(6.7), Inches(11.8), Inches(0.45))
            foot.fill.solid()
            foot.fill.fore_color.rgb = navy
            foot.line.fill.background()
            foot_box = current.shapes.add_textbox(Inches(1.0), Inches(6.8), Inches(11.3), Inches(0.2))
            p = foot_box.text_frame.paragraphs[0]
            r = p.add_run()
            r.text = f"Slide {offset}/{total_slides} | Executive analytics deck"
            r.font.size = Pt(9)
            r.font.color.rgb = white
            accent = current.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(12.0), Inches(0.45), Inches(0.35), Inches(0.35))
            accent.fill.solid()
            accent.fill.fore_color.rgb = gold
            accent.line.fill.background()
        presentation.save(resolved_output_path)
        return resolved_output_path

    resolved_pptx_path = Path(resolved_output_path)
    fallback_path = _preferred_output_path(
        str(resolved_pptx_path.with_name(f"{resolved_pptx_path.stem}_slide_deck_fallback.txt"))
    )
    lines = [
        slide_plan.get("presentation_title", "Analytics Report"),
        slide_plan.get("presentation_subtitle", "Executive slide deck"),
        "",
    ]
    for slide in slides:
        lines.append(f"Slide {slide.get('slide_number', '?')}: {slide.get('title', 'Untitled')}")
        for detail in slide.get("details", []):
            lines.append(f"  - {textwrap.fill(str(detail), width=100)}")
        visual = slide.get("visual_element", "")
        if visual:
            lines.append(f"  Visual: {visual}")
        lines.append("")
    Path(fallback_path).write_text("\n".join(lines), encoding="utf-8")
    return fallback_path
