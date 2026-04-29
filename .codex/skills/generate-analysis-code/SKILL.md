---
name: generate-analysis-code
description: Write and revise dataset-specific analysis code for the analytics workflow. Use when the Data Scientist Coder or reviewer needs guidance on classifying CSV columns, matching methods and visuals to variable types, and producing stronger evidence-backed findings instead of generic exploratory output.
---

# generate-analysis-code

Use this skill for `analytics_workflow` analysis generation and review.

## Goal
Produce analysis code that fits the actual dataset, answers the business question, and generates decision-useful visuals plus strong written findings.

## Use The User Description
Treat the user's dataset/business description as an explicit analysis parameter.

- Use it to identify the business objective, target audience, likely KPIs, and decision context.
- Prefer analyses that answer the user's stated goal over generic EDA.
- If the description mentions a target, segment, time horizon, market, product, customer group, risk, or decision, test whether the dataset supports that angle.
- If the data does not support part of the description, say so in `analysis_summary` and avoid unsupported claims.
- Include a `user_goal_alignment` entry in `analysis_summary` explaining how the analysis answers the user's description.

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

## Data Cleaning Engineer Pass
Before modeling or plotting, write code that profiles and prepares the data deliberately.

- Convert likely datetime columns with `pd.to_datetime(..., errors='coerce')`.
- Convert numeric-looking strings with `pd.to_numeric(..., errors='coerce')` only when most values parse successfully.
- Measure missingness by column and choose a simple, visible action: drop unusable columns, impute only when defensible, or keep missingness as a signal.
- Detect duplicates and decide whether duplicate rows are real repeated events or data quality issues.
- Detect numeric outliers with IQR or robust z-score. Do not delete outliers blindly; cap, flag, segment, or explain them based on the business context.
- Normalize category labels when needed: trim whitespace, collapse obvious case variants, and keep top categories with an `Other` bucket for long tails.
- Exclude identifier, free-text, and near-constant columns from correlation, clustering, and model features unless they are intentionally transformed.
- Record important cleaning decisions in `analysis_summary`.

## Choose Analysis By Variable Pairing
- `datetime + numeric`: line chart, rolling mean, growth, volatility, seasonality, before/after windows.
- `nominal + numeric`: grouped mean/median/sum, dispersion, ranked bar chart, box plot when spread matters.
- `ordinal + numeric`: ordered bar or line chart, monotonic trend across levels.
- `numeric + numeric`: correlation, scatter with fitted line, elasticity or driver analysis when relevant.
- `nominal + nominal`: crosstab, normalized stacked bar, concentration or mix analysis.
- `binary + numeric`: compare outcome rates, distributions, uplift, gap analysis.

## Choose Analysis Family
Pick the family that the dataset can genuinely support. Use more than one only when it adds evidence.

- `EDA`: Always do a compact EDA pass covering shape, missingness, distributions, key segments, and data quality risks.
- `Trend / time series`: Use only when reliable datetime or ordered period fields exist. Analyze trend, seasonality, rolling averages, volatility, and before/after changes.
- `Correlation`: Use numeric variables after removing identifiers and leakage-prone columns. Report strength and direction, not just a heatmap.
- `Association`: Use categorical pairs or binary outcomes. Use crosstabs, rates, lift, chi-square when helpful, and explain which segment differs.
- `Clustering`: Use only when there are enough meaningful numeric/encoded features and enough rows. Scale features, choose a small interpretable cluster count, and describe segment profiles.
- `Causal / driver analysis`: Do not claim causality from observational data unless there is a credible design such as time ordering, treatment/control, before/after, or quasi-experimental setup. Otherwise call it association or driver signal.
- `Prediction / classification`: Use only when a clear target exists. Avoid leakage, separate target/features, and report simple baseline-aware metrics.
- `Anomaly / outlier analysis`: Use when outliers are decision-relevant, such as fraud, risk, high-value customers, extreme demand, or operational exceptions.

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
- Do not run clustering, correlation, association, causal, or predictive analysis just because it is possible. Choose it because the data shape and question support it.
- When the dataset has no clear target or time column, focus on high-quality EDA, segmentation, association, and risk/opportunity patterns.
- When using correlation or clustering, preprocess features explicitly and summarize the features used.
- Keep every metric reproducible: define numerator, denominator, grouping, and filtering logic in code comments or summary keys.

## Step-By-Step Process
Generated code should follow this order:

1. Imports using installed and allowed analytics packages only.
2. Dataset copy and column role classification.
3. Cleaning and type conversion.
4. Missingness, duplicates, and outlier profiling.
5. Select analysis family from available fields and business goal.
6. Run 3 to 5 focused analyses with charts.
7. Save figures and populate `figure_captions`.
8. Populate `analysis_summary` with numeric evidence and cleaning notes.
9. Populate `business_findings` with concise evidence-backed findings.

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
