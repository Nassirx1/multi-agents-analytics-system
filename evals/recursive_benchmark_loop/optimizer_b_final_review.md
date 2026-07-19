# Recursive Optimizer B - Final Adversarial Review

## Verdict

**Benchmark assurance accepted; workflow candidate rejected.**

The original 100/100 exploit is closed in the real default execution path. Fabricated evidence strings and raw gate booleans fail assured scoring, and `run_default_catalog` now rejects overrides of the two canonical assured evaluators. The quality threshold is 85/100, above the preserved 76/90 boundary.

The default runner also verifies the executable catalog against the frozen `evals/benchmark_catalog.v1.json` fingerprint before running. Receipts now contain a deterministic semantic-result fingerprint that excludes clocks, durations, and output paths. Repeated negative-control runs produced the same fingerprint.

## Executed controls

The metamorphic evaluator ran on both routes and passed its declared controls for record order, whitespace, path relocation, keyword injection, stale evidence hashes, blank and clipped visuals, secret leakage and redaction, wrong-route artifacts, and unrelated charts. These are real executed cases, not prose-only policies.

Focused validation completed with **54 passing tests**.

## Preserved workflow failure

The refreshed full pilot produced **29 pass, 1 fail, 0 skip**. The failing case is `maa.agent.actionable_bounded_recommendations.v1`: the Decision Maker did not satisfy the required action fields, and the `unsupported_claims_absent` and `high_stakes_safety_clear` gates failed.

That failure is not an optimizer defect and was not weakened, skipped, or averaged away. It demonstrates that the benchmark now rejects a genuine workflow contract regression. The analytics workflow must not be described as fully benchmark-passing until that runtime contract is repaired in a separately authorized change and all 30 executions pass.

## Remaining benchmark limitations

The benchmark harness is useful and accepted for its current declared scope, but several defense-in-depth items remain:

- The named held-out case is an executable development control, not a secret independently frozen held-out set.
- Subjective criteria have anchors but no blinded inter-rater calibration.
- Legacy crosswalk equivalence lacks criterion-by-criterion mutation proof.
- Comparison fingerprints do not yet include every environment, renderer, evaluator, and fixture identity.
- Full suites are not automatically repeated three times.
- Render QA lacks an explicit all-page, all-slide, multi-viewport, multi-filter-state coverage matrix.
- A general optimizer diff allow-list and paired baseline/candidate rollback engine remain future work.

These limitations are recorded as open or partial in `optimizer_b_final.json`; none is represented as completed.
