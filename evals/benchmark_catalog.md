# Multi-Agent Analytics Benchmark Catalog v1

The executable source of truth is `benchmark_suite/catalog.py`. The catalog has
24 deterministic, credential-free cases with immutable IDs ending in `.v1`.
`benchmark_suite/rubrics.py` is the source of truth for output-quality scoring.

## Coverage

| Area | What is covered |
|---|---|
| Routing | Initial choice ordering, analytics isolation, HTML isolation, legacy resume |
| Shared stage | Data Understander profiling and quality contract |
| Analytics agents | Market Researcher, Analysis Planner, Data Scientist Coder, Code Reviewer, Business Translator, Decision Maker |
| Analytics outputs | PDF semantic QA, slide geometry/semantic QA, cross-artifact consistency |
| HTML agents | Dashboard Planning, HTML generation, dashboard QA negative mutations |
| Full workflows | Credential-free stubbed analytics and HTML end-to-end cases |
| Reliability | Checkpoint integrity, secret redaction, timeout and retry bounds |
| Quality | Four dimensions, route-specific hard gates, held-out metamorphic controls |

Every name in `ANALYTICS_REPORT_STEPS` and `HTML_DASHBOARD_STEPS` has at least
one direct benchmark. Runtime Power BI is deliberately absent; the HTML branch
is the system's BI output path.

## Output-quality score

Each output receives four independent scores from 0 to 100:

| Dimension | Overall weight | Minimum |
|---|---:|---:|
| Readability | 20% | 70 |
| Visibility | 20% | 70 |
| Trustworthiness | 35% | 75 |
| Executive suitability | 25% | 70 |

The weighted overall score must be at least 85, preserving the earlier 76/90
(84.44%) normalized gate. Dimension floors are
non-compensable: 100 in three dimensions cannot hide a failed fourth dimension.
Every positive observation needs an evidence locator such as an artifact path,
JSON pointer, page, slide, selector, hash, or calculation receipt. An unsupported
positive observation is capped at 50% and diagnosed. The deterministic check or
accountable human rater must also be recorded as provenance. Scores use explicit
0, 25, 50, 75, and 100 anchors and ambiguous values round down.

## Hard gates

The universal gates cover critical workflow failures, unsupported claims,
high-stakes safety, evidence consistency, calculation validity, openability,
artifact QA, rendered primary visuals, and sensitive-data protection. Report
outputs additionally require PDF semantic QA, slide geometry QA, and matching
evidence hashes. HTML outputs additionally require a self-contained document,
functional interactions, and dashboard QA. Missing gate results fail closed.

The 18 criteria in the earlier `evals/output_quality_rubric.md` remain mapped in
`LEGACY_CRITERIA_CROSSWALK`; none were silently discarded.

## Anti-gaming policy

- Measure artifacts and behavior, not rubric keywords or agent self-ratings.
- Require evidence anchors and independently recompute numeric checks.
- Keep fixed seeds, inputs, tolerances, versions, and versioned IDs.
- Use negative fixtures plus held-out metamorphic variants.
- Fail on hard-gate violations regardless of the weighted score.
- Report all criteria and diagnostics, including zeros and missing evidence.
- Reject improvements that reduce route isolation, trust, or reliability.

## Runner contract

`load_benchmark_catalog()` returns frozen `BenchmarkCase` instances. A runner
dispatches `case.target` (or its `case.check` alias), provides `case.fixture`,
and compares with `case.expected`. Results should record the benchmark ID,
catalog hash, status, elapsed time, evidence, diagnostics, hard gates, dimension
scores, and overall score in canonical JSON.
