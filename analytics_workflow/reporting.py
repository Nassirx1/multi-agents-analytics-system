from __future__ import annotations

import html
import json
import os
import re
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None

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


def _format_objective_coverage(workflow_state: dict[str, Any]) -> list[str]:
    objective = workflow_state.get("workflow_objective", {}) or {}
    analysis_summary = workflow_state.get("analysis_results", {}).get("analysis_summary", {}) or {}
    decision = workflow_state.get("agent_outputs", {}).get("decision_maker", {}) or {}
    items: list[str] = []
    raw_description = _stringify(objective.get("raw_description", ""))
    decision_question = _stringify(objective.get("decision_question", ""))
    if raw_description:
        items.append(f"Objective: {raw_description}")
    elif decision_question:
        items.append(f"Objective: {decision_question}")
    user_goal_alignment = ""
    if isinstance(analysis_summary, dict):
        user_goal_alignment = _stringify(analysis_summary.get("user_goal_alignment", ""))
    if user_goal_alignment:
        items.append(f"Analysis alignment: {user_goal_alignment}")
    final_recommendation = _stringify(decision.get("final_recommendation", ""))
    if final_recommendation:
        items.append(f"Decision answer: {final_recommendation}")
    limitations = objective.get("limitations", [])
    if limitations:
        items.append(f"Limitations: {'; '.join(_stringify(item) for item in limitations if _stringify(item))}")
    if not items:
        items.append("No user objective was provided; the workflow optimized for generally decision-useful findings.")
    return items


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
    findings: list[str] = []
    seen: set[str] = set()

    def add_finding(text: str) -> None:
        clean = _stringify(text).strip()
        if not clean:
            return
        if clean in seen:
            return
        seen.add(clean)
        findings.append(clean)

    for item in analysis_results.get("business_findings", [])[:6]:
        add_finding(item)

    summary = analysis_results.get("analysis_summary", {})
    if isinstance(summary, dict):
        for key, value in list(summary.items())[:6]:
            add_finding(f"{str(key).replace('_', ' ').title()}: {_stringify(value)}")

    figure_captions = analysis_results.get("figure_captions", {})
    if isinstance(figure_captions, dict):
        for figure_name, caption in list(figure_captions.items())[:6]:
            clean_caption = _stringify(caption).strip()
            if not clean_caption:
                continue
            figure_label = Path(str(figure_name)).stem.replace("_", " ").title()
            add_finding(f"{figure_label}: {clean_caption}")

    if not findings:
        add_finding("The analysis code executed, but no structured analysis findings were captured for reporting.")

    return findings


def _format_slide_analysis_findings(analysis_results: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    seen: set[str] = set()

    def add_finding(text: str) -> None:
        clean = _stringify(text).strip()
        if not clean or clean in seen:
            return
        seen.add(clean)
        findings.append(clean)

    for item in analysis_results.get("business_findings", [])[:4]:
        add_finding(item)

    summary = analysis_results.get("analysis_summary", {})
    if isinstance(summary, dict):
        for key, value in list(summary.items())[:6]:
            add_finding(f"{str(key).replace('_', ' ').title()}: {_stringify(value)}")

    if not findings:
        add_finding("The analysis code executed, but no structured analysis findings were captured for slides.")

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


def _format_priority_findings(business_translator: dict[str, Any]) -> list[str]:
    formatted: list[str] = []
    for item in business_translator.get("key_findings", [])[:5]:
        finding = _stringify(item.get("finding", ""))
        implication = _stringify(item.get("business_implication", ""))
        priority = _stringify(item.get("priority", ""))
        text = finding
        if implication:
            text += f" Business implication: {implication}."
        if priority:
            text += f" Priority: {priority}."
        if text:
            formatted.append(text)
    return formatted


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
    text = (caption or "").strip()
    if not text:
        return fallback_stem.replace("_", " ").title()
    sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    words = sentence.split()
    if len(words) > 9:
        sentence = " ".join(words[:9]).rstrip(",;:") + "..."
    return sentence.rstrip(".")


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
        title = _title_from_caption(caption, Path(figure).stem)
        expanded.append(
            {
                "slide_number": next_number,
                "title": title,
                "main_message": caption or f"Evidence from {Path(figure).stem.replace('_', ' ')}",
                "details": [],
                "visual_element": figure,
            }
        )
        next_number += 1

    return expanded


def _ensure_analysis_findings_slide(
    slides: list[dict[str, Any]],
    analysis_results: dict[str, Any],
) -> list[dict[str, Any]]:
    formatted_findings = _format_slide_analysis_findings(analysis_results)
    if not formatted_findings:
        return [dict(slide) for slide in slides]

    normalized_slides = [dict(slide) for slide in slides]
    analysis_keywords = ("analysis", "finding", "insight", "visual")

    for slide in normalized_slides:
        title = _stringify(slide.get("title", "")).lower()
        if any(keyword in title for keyword in analysis_keywords):
            existing_details = [str(item).strip() for item in (slide.get("details", []) or []) if str(item).strip()]
            merged_details: list[str] = []
            seen: set[str] = set()
            for item in existing_details + formatted_findings[1:5]:
                clean = _stringify(item).strip()
                if not clean or clean in seen:
                    continue
                seen.add(clean)
                merged_details.append(clean)
            if not _stringify(slide.get("main_message", "")).strip():
                slide["main_message"] = formatted_findings[0]
            slide["details"] = merged_details[:5]
            return _renumber_slides(normalized_slides)

    insert_at = 1 if len(normalized_slides) >= 1 else 0
    normalized_slides.insert(
        insert_at,
        {
            "slide_number": 0,
            "title": "Technical Analysis Findings",
            "main_message": formatted_findings[0],
            "details": formatted_findings[1:5],
            "visual_element": "",
        },
    )
    return _renumber_slides(normalized_slides)


def _ensure_objective_slide(slides: list[dict[str, Any]], workflow_state: dict[str, Any]) -> list[dict[str, Any]]:
    coverage = _format_objective_coverage(workflow_state)
    normalized = [dict(slide) for slide in slides]
    if any("objective" in _stringify(slide.get("title", "")).lower() for slide in normalized):
        return normalized
    normalized.insert(
        0,
        {
            "slide_number": 0,
            "title": "Objective Coverage",
            "main_message": coverage[0],
            "details": coverage[1:5],
            "visual_element": "",
        },
    )
    return _renumber_slides(normalized)


def _renumber_slides(slides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    renumbered: list[dict[str, Any]] = []
    for index, slide in enumerate(slides, start=1):
        updated = dict(slide)
        updated["slide_number"] = index
        renumbered.append(updated)
    return renumbered


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
    user_description = _stringify(workflow_state.get("user_data_description", "")).strip()
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
        if user_description:
            _add_pdf_body(story, f"User context: {user_description}", styles["ReportBody"])
        _add_pdf_body(story, outputs.get("decision_maker", {}).get("executive_summary", ""), styles["ReportBody"])
        _add_pdf_body(story, outputs.get("decision_maker", {}).get("decision_context", ""), styles["ReportBody"])

        _add_pdf_heading(story, "Objective Coverage", styles["ReportHeading"])
        _add_pdf_bullets(story, _format_objective_coverage(workflow_state), styles["ReportBody"])

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

                image_width, image_height = _fit_image_size(figure, 6.2 * inch, 3.6 * inch)
                story.append(RLImage(figure, width=image_width, height=image_height))
                if caption:
                    story.append(Paragraph(_safe_paragraph(f"Figure note: {caption}"), styles["ReportCaption"]))
                story.append(Spacer(1, 0.12 * inch))

        _add_pdf_heading(story, "Business Translation", styles["ReportHeading"])
        _add_pdf_body(story, outputs.get("business_translator", {}).get("executive_summary", ""), styles["ReportBody"])
        _add_pdf_body(story, outputs.get("business_translator", {}).get("business_narrative", ""), styles["ReportBody"])
        _add_pdf_bullets(story, _format_priority_findings(outputs.get("business_translator", {})), styles["ReportBody"])
        _add_pdf_bullets(story, outputs.get("business_translator", {}).get("opportunities", []), styles["ReportBody"])
        _add_pdf_bullets(story, outputs.get("business_translator", {}).get("risks", []), styles["ReportBody"])

        _add_pdf_heading(story, "Decision Recommendations", styles["ReportHeading"])
        _add_pdf_body(story, outputs.get("decision_maker", {}).get("final_recommendation", ""), styles["ReportBody"])
        _add_pdf_bullets(story, _format_recommendations(outputs.get("decision_maker", {}).get("recommendations", [])), styles["ReportBody"])
        _add_pdf_body(story, outputs.get("decision_maker", {}).get("conclusion", ""), styles["ReportBody"])

        _add_pdf_heading(story, "Appendix / Sources", styles["ReportHeading"])
        appendix_items = []
        for index, source in sorted(_source_index_map(outputs.get("market_researcher", {})).items()):
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
        f"User context: {user_description}" if user_description else "",
        _stringify(outputs.get("decision_maker", {}).get("executive_summary", "")),
        "",
        "Decision Context",
        _stringify(outputs.get("decision_maker", {}).get("decision_context", "")),
        "",
        "Objective Coverage",
        "\n".join(_format_objective_coverage(workflow_state)),
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
        "\n".join(_format_priority_findings(outputs.get("business_translator", {}))),
        "",
        "Recommendations",
        "\n".join(_format_recommendations(outputs.get("decision_maker", {}).get("recommendations", []))),
        _stringify(outputs.get("decision_maker", {}).get("conclusion", "")),
    ]
    Path(fallback_path).write_text("\n".join(lines), encoding="utf-8")
    return fallback_path


def generate_slide_deck(workflow_state: dict[str, Any], output_path: str = "analytics_report.pptx") -> str:
    from .deck_rendering import build_consulting_deck

    return build_consulting_deck(workflow_state, output_path)
