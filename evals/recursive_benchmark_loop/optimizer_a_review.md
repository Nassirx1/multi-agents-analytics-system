# Recursive Optimizer A — Pass 1 Review

Status: **proposal pending workflow baseline and adversarial review**  
Scope: Codex-side benchmark/evaluation infrastructure only. No analytics runtime changes are authorized.

## Baseline evidence

The authoritative pre-proposal quality definition is `evals/output_quality_rubric.md`. It contains 18 criteria, a 90-point maximum, a 76-point pass score, and hard gates for critical workflow failure, unsupported claims, high-stakes policy blockers, synchronized evidence hashes, and rendered semantic QA. `scripts/audit_run_trust.py` separately fails unless the PDF and slides are valid, their evidence hashes match, and the delivery bundle is complete.

No new benchmark execution score is claimed yet. The workflow evaluator has not produced its canonical receipt, so `execution_score` is explicitly `null` in the proposal. The unreceipted 43/50 sprint baseline and 47/50 legacy candidate are not comparable to the current 90-point rubric and must not drive acceptance.

## High-value gaps and failure modes

1. **HTML is invisible to the legacy output rubric.** The current criteria and success gate require PDF/PPTX artifacts but never validate the HTML dashboard path, filter behavior, self-containment, blank charts, or route isolation.
2. **Three score levels are undefined.** Only 0, 3, and 5 have anchors. A rater can assign 1, 2, or 4 without an observable standard, increasing drift and score inflation.
3. **The aggregate can hide a failed dimension.** Strong presentation scores can compensate for weak trustworthiness or executive suitability unless hard gates happen to catch the exact defect.
4. **Positive-only checks are gameable.** A real but unrelated chart, plausible unsupported prose, copied evidence IDs, or a mocked success receipt can look compliant without proving semantic correctness.
5. **Thresholds conflict.** The system requirements state 0.80 while 76/90 is approximately 0.844. A generic 0.80 implementation would weaken the current gate.
6. **Baseline provenance is weak.** Scores using a /50 denominator remain in the current rubric without a reproducible case list, evidence receipt, rater, timestamp, or current-denominator conversion.
7. **Flakiness controls are unspecified.** Exact prose matching, wall-clock thresholds, unordered results, rendering variance, and environmental dependencies can cause false regressions.

## Bounded proposal

The canonical proposal is `optimizer_a_proposal.json`. It contains the required baseline coverage, five changes, expected impact, risk, and a regression gate for every change. The first pass proposes:

- route-conditioned report and HTML gates;
- four separately reported quality dimensions with non-compensable floors;
- explicit observable anchors and evidence provenance;
- held-out negative plus metamorphic controls;
- canonical JSON receipts with derived human-readable Markdown.

This is not an instruction to weaken or replace existing gates. The normalized pass threshold cannot fall below 0.84, and every current trust gate is an invariant.

## Anti-gaming and determinism requirements

- Missing and skipped cases count as incomplete, never as passes.
- Optimizers may not edit held-out expected results.
- Trustworthiness cannot be offset by readability or visual polish.
- Qualitative exceptions must name evidence and cannot bypass binary gates.
- Repeated deterministic cases must return identical status and scores.
- Metamorphic variants may change harmless ordering, whitespace, and absolute paths without changing the result.
- Negative controls must include plausible-looking failures, not only malformed files.

## Initial decision

Do not accept a benchmark score improvement yet. Acceptance requires the workflow evaluator's baseline receipt, at least two adversarial critique rounds with Recursive Optimizer B, focused regression tests, and proof that report trust gates plus HTML route coverage remain intact.

