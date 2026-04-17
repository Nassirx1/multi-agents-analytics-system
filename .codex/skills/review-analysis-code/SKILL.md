# review-analysis-code

Use this skill when reviewing analysis code produced by the Data Scientist Coder Agent.

## Goal
Judge analytical quality, business relevance, chart usefulness, clarity, and reproducibility. Do not stop at syntax or runtime success.

## Gather First
- the dataset schema, sample rows, and known column meanings
- the business or user question the analysis is supposed to answer
- planning notes, market research notes, or reporting requirements if available
- the code, generated tables, and produced charts

## Review Checklist
### Fit To Dataset And Problem
- Check whether the chosen analysis matches the dataset shape, granularity, and target question.
- Flag generic exploratory output that does not meaningfully use the actual dataset.
- Reject methods that do not suit the variable types, sample size, time structure, or business objective.

### Analytical Logic
- Verify the analysis actually answers the stated business problem.
- Check whether assumptions are explicit and reasonable.
- Confirm that metrics, comparisons, segments, and time windows make sense for the decision context.
- Flag shallow analysis that produces activity without insight.

### Visualization Quality
- Check whether each chart communicates a specific useful point.
- Confirm chart type, labels, scales, and legends fit the data and do not mislead.
- Flag decorative or redundant plots.
- Prefer charts that directly support findings, tradeoffs, or recommendations.

### Interpretability And Reproducibility
- Check whether outputs are understandable by a downstream business translator.
- Confirm the code is structured enough to rerun with the same inputs and reproduce the same outputs.
- Flag missing artifact paths, unstable assumptions, or unclear dependencies.

## Required Output
Provide structured feedback with:

1. `What is good`
2. `What is wrong`
3. `What is missing`
4. `What to improve before approval`

Be strict. Approve only when the analysis is technically credible, business-relevant, and insight-generating.
