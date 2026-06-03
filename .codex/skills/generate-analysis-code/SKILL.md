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
- For pandas time grouping, avoid deprecated or version-fragile offset aliases in `resample`, `pd.Grouper`, and `date_range`. Use `h` for hourly, `D` for daily, `W` for weekly, `ME` for month-end, `QE` for quarter-end, and `YE` for year-end. Do not use bare `H`, `M`, or `Y` as offset frequencies; use `.dt.to_period('M').astype(str)` or `.dt.to_period('Y').astype(str)` for display labels when needed.
- Convert numeric-looking strings with `pd.to_numeric(..., errors='coerce')` only when most values parse successfully.
- Measure missingness by column and choose a simple, visible action: drop unusable columns, impute only when defensible, or keep missingness as a signal.
- Detect duplicates and decide whether duplicate rows are real repeated events or data quality issues.
- Detect numeric outliers with IQR or robust z-score. Do not delete outliers blindly; cap, flag, segment, or explain them based on the business context.
- Normalize category labels when needed: trim whitespace, collapse obvious case variants, and keep top categories with an `Other` bucket for long tails.
- Do not invent category values. Build category lists from observed values, value counts, or grouped rows; if a category is absent, record that limitation instead of indexing it directly.
- Use exact observed column names from `df.columns` and the data profile. If a likely analysis column is absent, choose an observed substitute and record the limitation.
- Parse compact numeric strings before numeric analysis: handle commas, percentages, and suffixes such as K, M, and B with a small helper, then use `pd.to_numeric(..., errors='coerce')`.
- Exclude identifier, free-text, and near-constant columns from correlation, clustering, and model features unless they are intentionally transformed.
- Record important cleaning decisions in `analysis_summary`.
- When binning numeric values, guard against pandas bin edge errors. For `pd.cut`, labels must be exactly one fewer than the final bin edges. For dynamic bins, omit labels or derive labels after deduping/sorting edges.
- Define `bin_edges` or `bins` before every `pd.cut` call and use one variable name consistently; skip bin-based visuals when the metric has too few unique values.
- For `pd.qcut(..., duplicates='drop')`, do not pass a fixed labels list unless you first know how many bins remain after duplicate edges are dropped.
- Do not call private sklearn or pandas internals. For imputation, use public `fit_transform` on selected numeric columns or simple median/mode fills.

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
- `Decision tree modeling`: Use only when the workflow explicitly provides a target column. Run it after EDA and the main analysis, choose classification or regression from the target type, and keep the tree shallow enough for stakeholder-readable rules.
- `Anomaly / outlier analysis`: Use when outliers are decision-relevant, such as fraud, risk, high-value customers, extreme demand, or operational exceptions.

## Visual Rules
- Create visuals that answer a question. Do not make random charts just to fill space.
- Prefer 3 to 5 strong figures over many weak ones.
- Each figure needs a narrative title, axis labels, and a caption that states the takeaway.
- Prefer focused EDA visuals that answer one question at a time, such as target rate by one category or one numeric distribution. Do not mix unrelated categorical dimensions in one chart unless the artifact is explicitly labeled as a cross-field scan.
- Figure captions must mention only fields that are visible in the chart.
- Style saved figures for slide reading with light neutral backgrounds, high-contrast labels, and a compact muted colorblind-aware palette using valid matplotlib colors such as `#1f4e79`, `#2a9d8f`, `#e9c46a`, `#4f772d`, and `firebrick`. Avoid invalid color names with spaces, neon colors, rainbow palettes, and red/green-only comparisons.
- For seaborn charts with `hue`, prefer a palette list. If a palette dictionary is needed, build it from the exact observed hue values after normalizing the hue column with `.astype(str)`; never hardcode partial keys like `{0: ..., 1: ...}` when plotted values may be `'0'` and `'1'`.
- Use line charts for ordered time or ordinal sequences, not arbitrary category order.
- Use bar charts for ranked comparisons, not long unsorted category dumps.
- Use box/violin plots only when distribution spread matters to the conclusion.
- Avoid pie charts unless there are very few categories and share-of-total is the real point.
- When possible, also produce `chart_specs` so the slide generator has structured fallback visuals if code-saved figures are unavailable.
- Prefer `analysis_artifacts` for all slide-worthy visuals. Produce at least four structured slide candidates so slides 4-7 can each rebuild an analysis chart. These artifacts must describe the chart as structured data plus meaning, not just an image path.
- Keep `chart_specs` small and aggregated: use short row dictionaries for the plotted values, never full raw dataframes.
- Supported `chart_type` values include `bar`, `column`, `grouped_bar`, `horizontal_bar`, `ranking`, `line`, `scatter`, `small_multiples_bar`, `distribution`, `metric_cards`, `comparison`, and `decision_tree`.
- Include `id`, `chart_type`, `title`, `takeaway`, optional `x`, optional `y`, optional `group_by`, optional `value_format`, optional `series`, and `data`.
- Prefer simple aggregated chart rows such as `{"category": "...", "value": 12.3}`, `{"label": "...", "value": 12.3}`, explicit `x/y` rows, or grouped rows with `group_by`; never provide only prose for slide-worthy visuals.
- For `analysis_artifacts`, include `artifact_id`, `artifact_type: chart_spec`, `slide_candidate: true`, `finding`, `chart_type`, `title`, `x_label`, `y_label`, `series` or `data`, `takeaway`, and `recommended_template`.
- For small multiples, provide separate named series with readable data points so the slide renderer can rebuild mini charts instead of pasting a 2x2 subplot image.

## Analysis Rules
- Answer the business problem first; generic EDA is not enough.
- Report concrete numbers in `analysis_summary` and `business_findings`.
- Explain what changed, how large the effect is, and why it matters.
- Surface missingness, outliers, and segment imbalance only when they affect trust or decisions.
- Exclude unusable columns deliberately and note why.
- Keep category ordering deterministic; never rely on accidental sort order.
- Do not run clustering, correlation, association, causal, or predictive analysis just because it is possible. Choose it because the data shape and question support it.
- Do not train a decision tree unless the user/workflow provided a target column. If the target is blank or absent, skip the model and record the skipped reason in `analysis_summary`.
- For classification trees, report training accuracy, test accuracy, and baseline accuracy. For regression trees, report training R2, test R2, train/test MAE, and note that R2 is the regression score rather than classification accuracy.
- If classification test accuracy is lower than baseline accuracy, describe the tree as an explanatory rule model only. Do not call it high accuracy, predictive lift, or production-ready.
- Export compact tree rules in `analysis_summary`, `business_findings`, and one structured `analysis_artifacts` item with `chart_type: decision_tree`, `artifact_id: decision_tree_rules`, `slide_candidate: true`, train/test metric fields, `fallback_path: decision_tree_rules.png`, and `data.nodes` plus `data.edges`.
- `build_sklearn_tree_artifact` and `render_decision_tree_rules_figure` are already available in the generated-code runtime. Call them directly; do not import fake helper modules such as `sklearn_utils` or `analytics_helpers`.
- Build decision tree rules from the fitted model so slides match the model. Prefer `build_sklearn_tree_artifact(fitted_tree_pipeline_or_model, feature_names=None, target=..., model_type=..., train_score=..., test_score=..., baseline_score=..., class_names=...)`, which extracts split and leaf rules directly from sklearn's fitted tree and marks `data.model_verified` and `data.rules_match_model`. Pass the fitted model instance or fitted Pipeline as the first positional argument; never pass `DecisionTreeClassifier`, `DecisionTreeRegressor`, aliases of those classes, `.tree_`, `model_or_pipeline=...`, or an unfitted class/property.
- Minimal allowed pattern: fit the model or Pipeline, compute `train_score`, `test_score`, and `baseline_score`, call `build_sklearn_tree_artifact(fitted_model_or_pipeline, feature_names=None, target=target_column, model_type='classification', train_score=train_score, test_score=test_score, baseline_score=baseline_score)`, then call `render_decision_tree_rules_figure(decision_tree_artifact, 'decision_tree_rules.png')` and append the artifact.
- A valid tree artifact must include at least one split node, at least two leaf nodes, non-empty edges, and true/false branch labels.
- Do not inspect sklearn tree internals yourself. Never read `DecisionTreeClassifier.tree_`, `DecisionTreeRegressor.tree_`, aliases such as `DTC.tree_`, `.feature`, `.threshold`, `.value`, or `.values` from sklearn classes/properties. Train a model instance or Pipeline, then pass that fitted object to `build_sklearn_tree_artifact(...)`; if using a Pipeline, `feature_names=None` is acceptable because the helper can infer transformed feature names.
- Save the decision tree diagram as `decision_tree_rules.png` after EDA figures, using `render_decision_tree_rules_figure(decision_tree_artifact, 'decision_tree_rules.png')`. The saved PNG must show split nodes and leaf prediction/rule nodes as rectangles connected by lines so PDF and slides reuse the code-generated visual.
- Leaf nodes must include the predicted class/value and the readable path/rule that reaches that leaf.
- When the dataset has no clear target or time column, focus on high-quality EDA, segmentation, association, and risk/opportunity patterns.
- When using correlation or clustering, preprocess features explicitly and summarize the features used.
- Keep every metric reproducible: define numerator, denominator, grouping, and filtering logic in code comments or summary keys.
- Avoid Unicode symbols in print/debug text. Prefer no print calls; if used, keep output ASCII-safe.
- For grouped summaries, use named aggregations on selected numeric metrics; do not call `.mean()` on a mixed dataframe or categorical dtype.
- When building chart artifacts, iterate over grouped rows or records. If a metric is a scalar, wrap it in a one-row data list instead of iterating the scalar.
- Avoid brittle metric-key indexing such as `row['median']`, `stats['rate']`, or `summary['count']` unless the key was just created in the same literal dict. Prefer named aggregations, iterating grouped records, or `.get('median', fallback)`.

## Step-By-Step Process
Generated code should follow this order:

1. Imports using installed and allowed analytics packages only.
2. Dataset copy and column role classification.
3. Cleaning and type conversion.
4. Missingness, duplicates, and outlier profiling.
5. Select analysis family from available fields and business goal.
6. Run 3 to 5 focused analyses with charts.
7. If a valid decision tree target was provided, train one classifier or regressor and prepare verified train/test metric and rule outputs from the fitted model.
8. Save figures and populate `figure_captions`.
9. Populate at least four `analysis_artifacts` for slide-worthy visuals using aggregated values and clear findings, including the `decision_tree` rules artifact when modeling ran.
10. Optionally populate `chart_specs` for simple slide-native visuals using aggregated values.
11. Populate `analysis_summary` with numeric evidence and cleaning notes.
12. Populate `business_findings` with concise evidence-backed findings.

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
- `analysis_artifacts`: list of structured chart definitions for slide-native rendering.
- `chart_specs`: optional backward-compatible list of structured chart definitions. This is additive and must not replace saved figures.

Reject or revise analysis that:
- ignores variable types
- treats ordinal fields as unordered
- uses weak or decorative visuals
- gives vague wording without numbers
- produces activity without insight
