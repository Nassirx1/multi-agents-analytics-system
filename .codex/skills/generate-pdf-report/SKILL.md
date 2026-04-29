# generate-pdf-report

Use this skill when generating or improving the final PDF report for the analytics workflow.

## Goal
Produce a professional, decision-ready PDF that covers the full workflow and ties evidence to recommendations.

## Use The User Description
- Treat the user's dataset/business description as the report objective and decision context.
- Include the description or a concise paraphrase near the Executive Summary.
- Use it to frame why the dataset, analysis choices, business translation, and recommendations matter.
- If the analysis cannot answer part of the user's stated goal, explain that limitation clearly.
- Recommendations should tie back to the user's stated decision context whenever possible.

## Use Existing Export Architecture First
- Reuse the current report generation pipeline, templates, and artifact folders when they exist.
- Improve structure and content quality before replacing any export mechanism.

## Required Report Structure
Create separate headings for:

1. Executive Summary
2. Dataset Overview / Data Understanding
3. Market Research
4. Analysis Plan
5. Data Analysis and Visual Findings
6. Business Translation
7. Decision Recommendations
8. Appendix / Sources

## Section Rules
### Market Research
- Explain the findings, not just the source list.
- Use inline numbered citations such as `[1]`, `[2]`, `[3]` for sourced claims.
- Keep the full source list in the appendix.
- Make each important claim traceable to a source.

### Business Translation
- Explain what the analysis code did in plain business language.
- Connect the analysis to the business problem, market research, and decision context.
- Focus on why the analysis matters, not only on technical steps.

### Decision Recommendations
- State what is recommended.
- State why it is recommended.
- State what evidence supports it.
- Tie recommendations back to data analysis, market research, and business interpretation.

## Quality Checks
- Cover the full workflow, not just the final model or final chart.
- Keep the report concise, complete, and free of filler.
- Make visuals readable and clearly captioned.
- Verify citations, appendix entries, and artifact export paths.
- Ensure the final document is polished enough for stakeholder delivery.
