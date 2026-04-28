# generate-slide-deck

Use this skill when generating, editing, or visually polishing the final PPTX deck for the analytics workflow (`analytics_workflow/reporting.py::generate_slide_deck` and the `presentation_architect` agent prompt). Do not use it for the PDF report — that belongs to `generate-pdf-report`.

## Goal
Produce a concise, executive-ready slide deck that summarizes the complete workflow with consulting-style clarity.

## Design Standard
- professional, practical, simple, concise, visually clean
- suitable for an executive audience
- favor strong structure, minimal clutter, and polished communication over flashy visuals

## Use Existing Deck Export First
- Reuse `generate_slide_deck` and its helpers (`_expand_slides_with_visuals`, `_title_from_caption`, `_style_run`) before introducing new exporters.
- Improve messaging, layout, and artifact quality before rebuilding the exporter.

## Preferred Slide Flow
1. Title / Context
2. Problem / Objective
3. Dataset Overview
4. Market Research Highlights
5. Analysis Approach
6. Key Findings / Visual Insights
7. Business Interpretation
8. Recommendations / Decision Options
9. Final Recommendation
10. Appendix, if needed

## Slide Rules
- Each slide carries exactly one main message (the colored callout band).
- Detail bullets must be rendered with a visible marker (current renderer uses `▪`) and stay short — wrap, do not truncate mid-sentence.
- Use a single font family across the whole deck (currently Calibri) for typographic consistency.
- Headers get a thin gold accent rule beneath them; the cover gets the same rule above the title.
- Use charts only when they add decision value. Avoid walls of text and decorative visuals.

## Visual Slide Rules (figures auto-promoted to slides)
- Title must be narrative, derived from the figure caption's first sentence, never the generic `Visual Insight N`.
- Caption appears once — in the colored callout — never duplicated as a body bullet.
- Picture is sized to leave breathing room above the footer; do not let it collide with the message band.

## Footer Rules
- Left foot: deck title (acts as running header).
- Right foot: `current / total` page indicator.
- Thin slate rule separates footer from body.

## Quality Checks
- Confirm the deck tells a coherent story from problem to recommendation.
- Keep visuals readable in presentation format.
- Ensure the final recommendation is evidence-backed and easy to defend.
- Verify export paths and artifact names if slide generation code changes.
- Run `tests/test_slide_deck.py` after any renderer change — it pins slide counts, picture counts, and forbidden legacy strings.
