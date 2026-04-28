---
name: generate-analysis-code
description: Write and revise dataset-specific analysis code for the analytics workflow. Use when the Data Scientist Coder or reviewer needs guidance on classifying CSV columns, matching methods and visuals to variable types, and producing stronger evidence-backed findings instead of generic exploratory output.
---

# generate-analysis-code

Use this skill for `analytics_workflow` analysis generation and review.

## Goal
Produce analysis code that fits the actual dataset, answers the business question, and generates decision-useful visuals plus strong written findings.

## Start With Column Roles
Classify each column before choosing methods.

- `identifier`: IDs, invoice numbers, order keys, UUID-like values. Do not chart raw IDs.
- `datetime`: dates, timestamps, periods. Use for trend, seasonality, rolling windows, before/after comparisons.
- `numeric-continuous`: revenue, price, duration, score, temperature. Use summary stats, distribution, trends, correlation, regression.
- `numeric-discrete`: counts, units, frequency, tickets, visits. Use count-aware summaries and comparisons.
- `binary`: yes/no, true/false, churned/not churned. Use rates, proportions, grouped comparisons.
- `nominal`: unordered categories such as region, product, department, channel. Use grouped summaries, ranked bars, crosstabs.
- `ordinal`: ordered categories such as low/medium/high, satisfaction scales, education levels, tenure bands. Preserve order in tables and charts.
- `free-text`: comments, descriptions, notes. Do not plot raw text; only use derived features if they clearly help.

If order is not explicit, infer only from common patterns like `low < medium < high`, Likert scales, weekdays, or month names. Otherwise treat as nominal.

## Choose Analysis By Variable Pairing
- `datetime + numeric`: line chart, rolling mean, growth, volatility, seasonality, before/after windows.
- `nominal + numeric`: grouped mean/median/sum, dispersion, ranked bar chart, box plot when spread matters.
- `ordinal + numeric`: ordered bar or line chart, monotonic trend across levels.
- `numeric + numeric`: correlation, scatter with fitted line, elasticity or driver analysis when relevant.
- `nominal + nominal`: crosstab, normalized stacked bar, concentration or mix analysis.
- `binary + numeric`: compare outcome rates, distributions, uplift, gap analysis.

## Visual Rules
- Create visuals that answer a question. Do not make random charts just to fill space.
- Prefer 3 to 5 strong figures over many weak ones.
- Each figure needs a narrative title, axis labels, and a caption that states the takeaway.
- Use line charts for ordered time or ordinal sequences, not arbitrary category order.
- Use bar charts for ranked comparisons, not long unsorted category dumps.
- Use box/violin plots only when distribution spread matters to the conclusion.
- Avoid pie charts unless there are very few categories and share-of-total is the real point.

## Analysis Rules
- Answer the business problem first; generic EDA is not enough.
- Report concrete numbers in `analysis_summary` and `business_findings`.
- Explain what changed, how large the effect is, and why it matters.
- Surface missingness, outliers, and segment imbalance only when they affect trust or decisions.
- Exclude unusable columns deliberately and note why.
- Keep category ordering deterministic; never rely on accidental sort order.

## Writing Rules
Use strong, evidence-based wording.

- Prefer: `Region A generated 18.4% more revenue than Region B, driven by higher average order value.`
- Prefer: `Complaints rise sharply after month 6, suggesting a retention risk window.`
- Avoid: `There seems to be some difference.`
- Avoid: `The chart looks random but maybe useful.`

Each finding should include:
- the pattern
- the number or magnitude
- the business implication

## Required Output Contract
- `analysis_summary`: dict with numeric evidence and concise technical/business metrics
- `business_findings`: list of evidence-backed insight statements
- `figure_captions`: dict mapping each saved figure to a one-sentence interpretation

Reject or revise analysis that:
- ignores variable types
- treats ordinal fields as unordered
- uses weak or decorative visuals
- gives vague wording without numbers
- produces activity without insight
