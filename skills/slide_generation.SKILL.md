# Slide Generation Skill

## Purpose
Build an executive slide story from workflow outputs and visual analysis evidence.

## Inputs
- Workflow state, deck plan, saved figures, structured chart artifacts, findings, recommendations, and limitations.

## Expected Outputs
- Deterministic deck specification and rendered PPTX with concise, visual-first analysis slides.

## Hard Rules
- Keep slide rendering runnable from the normal Python workflow.
- Prefer code-saved figures for EDA slides and structured chart artifacts as fallback.
- Render `decision_tree` artifacts as rule boxes connected by lines; do not flatten model rules into plain bullets when structured nodes/edges are available.
- Decision tree slides must show baseline comparison and call under-baseline trees explanatory only.
- Keep headlines to roughly 11-14 words, main messages to roughly 18-24 words, and recommendation table cells as short action/evidence/priority phrases.
- Use action headlines, concise bullets, and clear visual takeaways.
- Keep analysis-slide copy on evidence and implication; do not spend bullets on meta-instructions about interpreting the slide.
- Optimize for human favorability: each slide should feel calm, easy to scan, credible, and useful in a meeting.
- Use one dominant idea per slide. If a chart needs explanation before it can be understood, simplify the visual or split the story.
- Make every slide title match the visible evidence, not a previous slide or a generic theme.
- Reject or demote dense stock/time-series risk figures unless the slide title states a clear decision lens and the chart visibly annotates the relevant window, threshold, or regime.
- Business translation slides must not repeat the evidence-summary slide; translate evidence into decision use, tradeoff, operating rule, and validation.
- Put caveats close to weak or sensitive evidence so the audience does not have to remember them later.

## Failure Cases To Avoid
- Text-only analysis slides when usable visuals exist.
- Decision tree model rules shown only as prose instead of a structured rule diagram.
- Invented visual paths or broken placeholders.
- Crowded slides that repeat report paragraphs.
- Dense multi-panel visuals at normal slide size when a focused chart would communicate better.
- Recommendation slides with generic actions, long cells, or text that feels like raw workflow output.

## Quality Checklist
- The deck tells a top-down story.
- Analysis visuals match the evidence used in reporting.
- Recommendations and limitations remain visible.
- A human executive can understand each slide in a few seconds: title, visual, takeaway, and caveat point in the same direction.
- Sensitive-domain decks sound respectful and non-alarmist while still being clear about risk.

## Example
Use a ranked bar chart plus a short implication block for a segment comparison instead of a table of every raw value.
