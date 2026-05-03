# generate-slide-deck

Use this skill when generating, editing, or visually polishing the final PPTX deck for the analytics workflow. It applies to `analytics_workflow/reporting.py::generate_slide_deck`, `analytics_workflow/deck_rendering.py`, and the `PresentationArchitectAgent` prompt. Do not use it for PDF reporting.

## Goal
Produce a concise, executive-ready consulting-style deck that summarizes the full workflow with clear business takeaways, visual evidence, and recommendations.

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
4. Visual analysis slides for actual generated figures.
5. Business interpretation and implications.
6. Recommendations or decision options.
7. Limitations and next steps.
8. Closing message.

## Slide Schema
Each planned slide should follow this shape:

```json
{
  "slide_number": 1,
  "layout_type": "chart_with_takeaways",
  "title": "Action-oriented slide title",
  "main_message": "The key point of the slide",
  "details": [
    "Short supporting point 1",
    "Short supporting point 2",
    "Short supporting point 3"
  ],
  "visual_path": "path/to/chart.png",
  "visual_type": "line_chart",
  "visual_caption": "Short explanation of the visual",
  "visual_takeaway": "What the viewer should conclude from the visual",
  "business_implication": "Why this matters",
  "speaker_note": "Optional presenter note"
}
```

Legacy `visual_element` is accepted for backward compatibility, but new plans should use `visual_path`.

## Layout Types
- `cover`: title slide only.
- `executive_summary`: main answer and key takeaway cards.
- `dataset_overview`: objective, dataset/business context, quality, and scope.
- `kpi_cards`: metric-heavy summary with short labels.
- `chart_focus`: one large visual with a takeaway band.
- `chart_with_takeaways`: visual plus concise supporting bullets.
- `two_column_insight`: evidence on one side, implication on the other.
- `insight_cards`: 3 to 4 compact finding cards.
- `recommendation_matrix`: action, rationale, evidence, impact/timeline.
- `risk_limitations`: limitations, risks, and next checks.
- `closing`: final recommendation or next-step message.

## Content Rules
- Use action titles, not generic labels.
- Each slide should have one main message.
- Keep bullets short, business-facing, and evidence-backed.
- Avoid crowded paragraphs, generic AI filler, and repeated slide structures.
- Use the user's description to explain why the slide matters.
- Recommendations must connect to data analysis, market context, and business interpretation when available.

## Visual Rules
- Include visual analysis outputs only when the file exists.
- Each saved figure should appear on at most one visual slide.
- Use `visual_path`, `visual_type`, `visual_caption`, and `visual_takeaway` for actual visuals.
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
- Reject invalid layout types or fall back to a safe layout.
- Remove missing visual paths and use a clean non-visual layout.
- Watch for overcrowded slide risk.

## Test Expectations
- Run `tests/test_slide_deck.py` after renderer changes.
- Confirm saved figures become visual slides without duplicate captions.
- Confirm missing visuals do not create broken placeholders.
- Confirm legacy `visual_element` still works.
- Confirm the deck includes objective/data-description context when provided.
