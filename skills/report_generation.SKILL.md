# Report Generation Skill

## Purpose
Create a professional PDF report that reflects the full multi-agent workflow.

## Inputs
- Workflow state, generated figures, market citations, business translation, decisions, and limitations.

## Expected Outputs
- Report artifact with executive summary, objective coverage, data overview, market research, analysis plan, visual findings, business translation, recommendations, and sources.

## Hard Rules
- Preserve evidence paths and source traceability.
- Use code-saved figures when available and captions that explain the takeaway.
- When a decision tree artifact is present, show its verified split/leaf rules as connected rectangles and include train/test/baseline metrics beside the diagram.
- If the decision tree underperforms baseline, state that it is explanatory only and avoid production-prediction wording.
- Give each important figure a short evidence heading and a takeaway caption so the report remains skimmable around visuals.
- Keep stakeholder prose concise and readable.
- Optimize for human readability and favorability: make the first page answer "what should I do, why should I trust it, and what should I be careful about?"
- Remove raw workflow, debug, prompt, or model-dump language unless it is clearly labeled as an appendix diagnostic.
- Use short paragraphs, plain-language headings, and early caveats so a stakeholder can scan the report without feeling buried.
- Recommendations should expose owner, trigger, timeline, expected impact, guardrail, and risk where available; avoid generic verbs without operating thresholds.
- Do not let appendix diagnostics or trace notes create a mostly blank trailing page; keep workflow trace out of the polished PDF unless there are no external sources or it is explicitly requested.

## Failure Cases To Avoid
- Reporting only final recommendations without the evidence chain.
- Broken figure references or unsourced market claims.
- Decision tree rules rendered only as paragraph text when structured nodes and edges are available.
- Treating fallback text output as a polished PDF without noting the fallback.
- Dense pages where the decision, evidence, caveat, and next action are hard to find.
- Mostly blank trailing pages or appendix pages that contain only tiny diagnostic text.
- Machine-like phrasing that sounds impressive but does not help a human reader decide.

## Quality Checklist
- Required sections appear in the generated report surface.
- Figures and citations are readable and relevant.
- Limitations and objective coverage are explicit.
- Executive summary, visual findings, and recommendations pass a reader-friction check: clear, calm, credible, and pleasant to scan.
- Sensitive-domain reports use respectful, non-diagnostic wording and keep human review visible.

## Example
Place a figure note immediately after a chart so a reader understands the visual evidence without searching the appendix.
