# generate-slide-deck

Use this skill when generating, editing, or visually polishing the final PPTX deck for the analytics workflow. It applies to `analytics_workflow/reporting.py::generate_slide_deck`, `analytics_workflow/deck_rendering.py`, `analytics_workflow/slides/`, and the `PresentationArchitectAgent` prompt. Do not use it for PDF reporting.

## Goal
Produce a concise, executive-ready consulting-style deck that summarizes the full workflow with clear business takeaways, visual evidence, and recommendations.

The slide generator must run from normal Python execution. Do not make runtime generation depend on Codex, MCP, a browser, a PowerPoint add-in, or a remote repository. Codex skills are development guidance only.

## Architecture
- `story_builder.py` converts workflow outputs and LLM story plans into a structured `DeckSpec`.
- `deck_spec.py` owns the deck, slide, content block, and visual contracts.
- `templates.py` owns template names and layout intent.
- `theme.py` owns design constraints.
- `chart_renderer.py` rebuilds visuals from structured analysis artifacts first, then uses native PowerPoint charts or theme-matched matplotlib fallback, with legacy images as last resort.
- `pptx_renderer.py` owns exact spacing, fonts, alignment, and deterministic rendering.
- `validators.py` checks and repairs basic slide quality issues without a vision model.

## Data Description Parameter
- Treat the user's dataset/business description as a formal deck input.
- Use it to frame the title, context, storyline, slide titles, visual takeaways, implications, recommendations, and limitations.
- If the data does not support part of the user's stated context, say that as a limitation or next step.
- The runtime may expose this as `data_description`, `user_data_description`, or `workflow_objective.raw_description`; use the first available non-empty value.

## Storyline
Use top-down consulting logic:
1. Executive answer / main message.
2. Business context and dataset overview.
3. Key findings and supporting evidence.
4. Visual analysis slides from structured findings and chart artifacts.
5. Business interpretation and implications.
6. Recommendations or decision options.
7. Limitations and next steps.
8. Closing message.

## Deck Spec Schema
Each planned deck should follow this shape:

```json
{
  "deck_title": "...",
  "audience": "executive",
  "theme": "consulting_minimal",
  "dataset_context": {
    "name": "...",
    "description": "...",
    "rows": null,
    "columns": null,
    "target": null,
    "data_quality_notes": []
  },
  "slides": [
    {
      "slide_number": 4,
      "slide_role": "analysis",
      "template": "chart_left_insight_right",
      "headline": "Insight-driven slide headline",
      "main_message": "The key point of the slide",
      "visual": {
        "type": "structured_chart",
        "chart_type": "bar",
        "artifact_id": "chart_1",
        "data": [{"category": "A", "value": 12.3}],
        "x_label": "Segment",
        "y_label": "Rate (%)",
        "value_format": "{:.1f}%",
        "takeaway": "What the viewer should conclude"
      },
      "content_blocks": [
        {"type": "bullets", "items": ["Short supporting point"]}
      ]
    }
  ]
}
```

Legacy `visual_element` and `visual_path` are accepted for backward compatibility. Default EDA slides should use code-saved figures when they exist so slide visuals match PDF figures; structured chart artifacts remain the fallback path.

## Template Registry
- `title_cover`
- `data_understanding_overview`
- `market_context_bullets`
- `chart_left_insight_right`
- `chart_right_insight_left`
- `full_width_chart_takeaway`
- `metric_strip_plus_chart`
- `small_multiples_with_takeaway`
- `single_bar_chart_with_insight`
- `horizontal_bar_ranking`
- `metric_cards_with_chart`
- `comparison_chart_with_interpretation`
- `distribution_with_callout`
- `segment_profile_cards`
- `three_finding_cards`
- `comparison_matrix`
- `recommendation_priority`
- `limitations_professional`
- `executive_summary_closing`

These are flexible layout blueprints, not static finished slides. The LLM chooses the slide role, template, content blocks, visual intent, chart type, and emphasis. The renderer controls margins, coordinates, font sizes, alignment, colors, and chart styling.

## Default Slide Order
1. Title Slide
2. Data Understanding
3. Market Search / Business Context
4. EDA Visual Analysis
5. EDA Visual Analysis
6. EDA Visual Analysis
7. EDA Visual Analysis
8. Evidence Findings
9. Business Translation
10. Recommendations
11. Limitations
12. Ending Summary

## Content Rules
- Use action titles, not generic labels.
- Each slide should have one main message.
- Keep bullets short, business-facing, and evidence-backed.
- Avoid crowded paragraphs, generic AI filler, and repeated slide structures.
- Use the user's description to explain why the slide matters.
- Recommendations must connect to data analysis, market context, and business interpretation when available.

## Visual Rules
- Prefer code-saved figures from `workflow_state.saved_figures` for slides 4-7 so EDA slides show the same analysis-code visuals used by the PDF.
- Use structured chart artifacts from `analysis_results.analysis_artifacts` and `analysis_results.chart_specs` only when a code-saved figure is unavailable.
- Analysis findings and figure captions should briefly explain each chart; they must not replace the visual when a code figure exists.
- Use native PowerPoint charts for simple bar, column, line, and scatter specs when possible.
- Use controlled PowerPoint shape rendering for bar, grouped bar, horizontal ranking, line, small multiples, metric cards, and comparison visuals when native chart rendering is not the best fit.
- Render `decision_tree` artifacts as model-rule rectangles connected by lines, with accuracy/R2/MAE context visible near the diagram.
- Use theme-matched matplotlib fallback when native or shape rendering is not possible.
- Include a saved figure only when the file exists.
- Each saved figure should appear on at most one EDA visual slide.
- Use `visual_path`, `visual_type`, `visual_caption`, and `visual_takeaway` only for actual figure-backed visuals.
- Do not request or render image placeholders when no real visual exists.
- Visual slides should reserve a clean, well-sized visual area and preserve image aspect ratio.
- Non-visual slides should remain polished without empty image boxes.

## Style Standard
- Professional, practical, simple, concise, visually clean.
- Light neutral background, dark text, subtle accent color, strong whitespace.
- Consistent font family, color palette, footer, title hierarchy, and spacing.
- Consulting-company style clarity without flashy decoration or clutter.

## Validation Checks
- Flag or repair overly long titles.
- Require a non-empty main message.
- Keep details to a small number of short bullets.
- Reject invalid templates or fall back to a safe layout.
- Remove missing visual paths and use a clean non-visual layout.
- Warn when an analysis slide has neither a code figure nor a renderable structured fallback.
- Warn when an analysis slide is text-only, has an unsupported chart type, or has chart data that cannot be rendered.
- Watch for overcrowded slide risk.
- Check the 12-slide default structure and repeated layouts.

## Test Expectations
- Run `tests/test_slide_deck.py` after renderer changes.
- Confirm saved figures become visual slides without duplicate captions.
- Confirm missing visuals do not create broken placeholders.
- Confirm legacy `visual_element` still works.
- Confirm the deck includes objective/data-description context when provided.
- Confirm structured chart specs render before saved figures.
- Confirm small multiples are rebuilt from data rather than pasted as subplot images.
- Confirm chart specs can render or fall back without crashing.
- Confirm decision-tree rules render from structured nodes and edges rather than plain text only.
