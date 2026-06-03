# Code Generation Skill

## Purpose
Generate reproducible analysis code that follows the plan and produces workflow-ready artifacts.

## Inputs
- Analysis plan, CSV runtime variables, data profile, objective context, and review feedback.

## Expected Outputs
- Executable Python code.
- `analysis_summary`, `business_findings`, `figure_captions`, and `analysis_artifacts`.
- Saved figures and optional compact `chart_specs` for slide fallback.
- When the workflow provides a decision tree target column, model metrics and a structured `decision_tree` rules artifact.

## Hard Rules
- Use only approved analytics packages and the provided dataframe variables.
- Never call shell tools, package installers, network clients, `open()`, `eval()`, or `exec()` from generated analysis code.
- Save figures with direct `figure_#.png` names. Do not import filesystem helpers such as `os` or `pathlib` in generated analysis code.
- Produce numeric evidence and aggregate chart data instead of decorative output.
- Keep charts readable for both PDF and slide use: one readable chart per PNG, large labels, clear legends, and visible annotations for the claimed takeaway.
- Choose visuals from the data-understanding column roles and suited visual plan; do not generate random filler charts.
- Prefer focused EDA visuals that answer one question at a time. Do not mix unrelated categorical dimensions in one chart unless the chart is explicitly labeled as a cross-field scan.
- For credit-risk or lending datasets, prioritize readable binned/ranked visuals such as loan grade, debt burden (`loan_percent_income`), income bands, interest-rate buckets, home ownership, and prior default flags. Avoid standalone age or scatter charts unless they directly answer a stated decision question.
- For stock or no-target time-series datasets, parse dates, sort chronologically, parse compact volume strings such as `331.82K` or `2.83M`, and convert percent-change strings before analysis. Prefer price trend with moving averages/support-resistance, volume/liquidity spikes, return distribution, and seasonal/monthly return summaries. Use at most one rolling/windowed volatility or drawdown risk figure unless the user explicitly requests more; avoid dense multi-panel risk plots and dense raw `volume` versus `return`/`Change %` scatter as default report or slide visuals.
- For lending recommendations, frame actions as validation pilots with a target segment, evidence, pilot metric, and governance caveat. Do not claim reduced credit losses unless the script explicitly calculates that impact.
- Captions must only mention fields visible in the chart.
- Build EDA charts from aligned aggregated rows: every category, value, label, and color sequence passed to a chart must have matching lengths.
- Use explicit valid matplotlib color strings or one color per plotted group. Do not use informal color names with spaces such as `brick red`, and do not pass partial color arrays assembled from mismatched segments.
- For seaborn charts with `hue`, prefer a palette list. If a palette dictionary is needed, build it from the exact observed hue values after normalizing the hue column with `.astype(str)`; never hardcode partial keys like `{0: ..., 1: ...}` when plotted values may be `'0'` and `'1'`.
- Prefer a complete bounded script over a broader script that risks truncation: use 3 to 5 focused figures, compact helpers only when they reduce repetition, and assign the required output contract before optional deep-dive sections.
- Use modern pandas missing-value calls in generated code: prefer `.ffill()` and `.bfill()` instead of deprecated `fillna(method=...)`.
- Use pandas-safe time frequencies in `resample`, `pd.Grouper`, and `date_range`: prefer `h`, `D`, `W`, `ME`, `QE`, and `YE`; avoid bare `H`, `M`, and `Y` offset aliases that fail on newer pandas versions.
- Treat long-form `metric`/`value` summary tables as label-driven data: inspect the observed metric labels, preserve their raw names in lookups, and check presence before using a metric value.
- Avoid brittle metric-key indexing such as `row['median']` or `stats['rate']` unless that key was just created in the same literal dict. Prefer named aggregations, iterating grouped records, or `.get('median', fallback)`.
- Keep numeric metric values numeric. Put month names, category labels, and other display strings in separate variables or label columns instead of assigning them into a numeric value column.
- Use exact observed column names and category values from the profile. If a desired column or category is absent, choose an observed substitute and record the limitation.
- Parse compact numeric strings such as `331.82K`, `1.2M`, percentages, and comma-formatted values before numeric analysis.
- Convert categorical labels to strings before concatenation and numeric series to numeric values before arithmetic or aggregation.
- Use named aggregations on selected numeric metrics; never call `.mean()` on a mixed dataframe or categorical dtype.
- Do not call private sklearn or pandas internals. Use public `fit_transform` or simple median/mode fills for imputation.
- Build chart artifacts from grouped row records; wrap scalar metrics in one-row data lists instead of iterating them.
- Train a decision tree only when the workflow explicitly provides a target column and that target exists in the data. If no target is provided, skip modeling and record the skip in `analysis_summary`.
- Place decision tree modeling after EDA and the main analysis. Use a classifier for binary/categorical/low-cardinality discrete targets and a regressor for continuous numeric targets.
- Report classification accuracy plus baseline accuracy, or regression R2 plus MAE with a note that R2 is the regression score.
- If classification test accuracy is lower than baseline accuracy, describe the tree as an explanatory rule model only and do not claim high accuracy or predictive lift.
- Export compact model rules and include a `decision_tree` artifact whose `data` contains small `nodes` and `edges` lists so report and slide renderers can draw rule rectangles and connecting lines.
- The decision-tree helper functions are already available in the generated-code runtime. Call `build_sklearn_tree_artifact(...)` and `render_decision_tree_rules_figure(...)` directly; do not import fake helper modules such as `sklearn_utils` or `analytics_helpers`.
- Build tree artifacts with `build_sklearn_tree_artifact(fitted_tree_pipeline_or_model, feature_names=None, ...)`; pass the fitted model instance or fitted Pipeline as the first positional argument, not `DecisionTreeClassifier`, `DecisionTreeRegressor`, aliases of those classes, `.tree_`, `model_or_pipeline=...`, or any custom model keyword.
- Never read decision-tree internals in generated code, including `DecisionTreeClassifier.tree_`, `DecisionTreeRegressor.tree_`, aliases such as `DTC.tree_`, `.feature`, `.threshold`, `.value`, or `.values`; the helper extracts verified rules from the fitted object.
- Minimal allowed pattern: fit the model or Pipeline, compute `train_score`, `test_score`, and `baseline_score`, call `build_sklearn_tree_artifact(fitted_model_or_pipeline, feature_names=None, target=target_column, model_type='classification', train_score=train_score, test_score=test_score, baseline_score=baseline_score)`, then call `render_decision_tree_rules_figure(decision_tree_artifact, 'decision_tree_rules.png')` and append the artifact.
- The `decision_tree` artifact must include at least one split node, at least two leaf nodes, non-empty edges, and true/false branch labels.
- If preprocessing scales numeric features, preserve exact verified model metadata but make visible tree labels stakeholder-readable. Prefer inverse-transformed thresholds only when safe; otherwise label them honestly as model-scaled lower/higher ranges.
- For imbalanced classification, include baseline comparison and, when available, precision, recall, F1, positive-class rate, and support. Accuracy alone is not enough.

## Failure Cases To Avoid
- Inventing columns or targets absent from the profile.
- Inventing normalized metric names that are not present in a long-form summary table.
- Creating EDA charts with unequal labels/values/colors or arithmetic directly on pandas categorical dtype.
- Using raw row-level stock volume/return scatter as filler when trend, volatility, drawdown, volume spike, return distribution, or seasonality visuals would better answer the time-series decision.
- Ignoring a target, date, segment, or numeric driver that the data-understanding step already identified.
- Training a decision tree without an explicit target column from the user/workflow.
- Reporting decision tree rules only as prose instead of a structured artifact.
- Showing standardized thresholds such as `Age <= 0.279` as if they were original business units.
- Returning prose, JSON, or markdown instead of a runnable script.
- Producing findings with no magnitude, chart context, or business implication.

## Quality Checklist
- Code parses and reruns with the supplied dataframes.
- Required artifact variables are assigned.
- Cleaning, role handling, and limitation notes are visible.
- At least four slide-worthy structured artifacts are present when the data supports them.
- The script is concise enough for one model response and does not duplicate the same chart payload in both prose and large raw tables.

## Example
Return a script that saves `figure_1.png`, records its takeaway in `figure_captions`, and places its aggregated values and finding in `analysis_artifacts`.
