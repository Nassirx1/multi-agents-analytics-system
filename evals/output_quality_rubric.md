# Output Quality Rubric

Score each criterion from 0 to 5.

| # | Criterion | 0 | 3 | 5 |
|---|---|---|---|---|
| 1 | EDA charts are real visuals, not only text | No visuals | Some placeholder or text-heavy visuals | Real chart images or rendered structured charts are present |
| 2 | Charts match dataset and analysis logic | Unrelated or generic charts | Partially aligned visuals | Visuals reflect actual dataset columns, aggregations, and findings |
| 3 | Slides follow a clear executive story | Random report sections | Mostly coherent sequence | Top-down decision story from context to evidence to action |
| 4 | Slide order is logical | Confusing order | Mostly usable | Clear sequence: title, data, context, visual analysis, findings, translation, recommendations, limitations, close |
| 5 | Slide titles are insight-driven | Generic labels | Mixed labels and insights | Titles state what the viewer should conclude |
| 6 | Each visual slide has one clear message | Visuals lack takeaways | Some slides have messages | Every visual slide pairs one chart with one concise takeaway |
| 7 | Report has useful analysis, not generic text | Boilerplate | Some useful evidence | Specific analysis, figures, workflow trace, and decision context |
| 8 | Recommendations are specific and data-supported | Generic actions | Partially evidence-backed | Actions include rationale, evidence, impact, and practical guardrails |
| 9 | Workflow runs without errors | Critical workflow failure | Runs with warnings or limited simulation | Deterministic tests and smoke checks pass without critical failure |
| 10 | Outputs are saved correctly | Missing outputs | PDF/PPTX saved, weak traceability | PDF, PPTX, figures, and structured JSON artifacts are saved and referenced |
| 11 | Claims trace to evidence IDs | No traceability | Some sources are named | Every decision claim resolves to dataset, EDA, model, or external-source evidence |
| 12 | Numbers include scope and denominator | Rates lack a base | Scope is available elsewhere | Numerator/denominator, cohort, filter, and time basis are visible or available in notes |
| 13 | Statistical and model uncertainty is honest | Accuracy or rankings are overstated | Caveats are generic | Significance/effect size or omission reason is present; imbalanced models show class-sensitive metrics and support |
| 14 | Recommendations remain inside evidence | Invented thresholds/features | Evidence is loosely related | Every action cites evidence IDs and has owner, trigger, metric, stop condition, and validation status |
| 15 | High-stakes safety is enforced | Automated or diagnostic action is encouraged | Human review is mentioned | Domain-expert review, privacy/fairness/safety checks, and prohibited-use boundaries are explicit |
| 16 | PDF and slides are synchronized | Conflicting final stories | Mostly aligned | Both derive from the same evidence-bundle hash and quality receipt |
| 17 | Rendered PDF is readable | Broken glyphs/JSON/dense pages | Minor density issues | Unicode-safe, answer-first, page-numbered, visually balanced, and passes rendered semantic QA |
| 18 | Rendered slides are readable | Clipped, repetitive, or source-free | Minor warnings | Visual-first, non-repetitive, evidence-labelled, and passes geometry plus semantic QA |

Total: 90.

Pass if score >= 76, no critical workflow failure, no unsupported claim or high-stakes policy blocker, PDF and PowerPoint share the same evidence hash, and both rendered artifacts pass semantic QA.

Legacy accepted candidate score: 47/50 under the previous presentation-focused rubric.

Baseline score for this sprint: 43/50.
