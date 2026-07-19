# Recursive Optimizer B - Pass 1 Adversarial Review

Status: **reject pass 1 pending corrective round 2**  
Scope: Codex-side benchmark and evaluation infrastructure only. This review does not authorize analytics runtime changes or weaker gates.

## Decision

Optimizer A correctly identified route isolation, dimension floors, negative controls, evidence provenance, and readable receipts as priorities. The proposal is not yet safe to accept because those controls are mostly policy statements rather than enforced boundaries.

The strongest counterexample is reproducible: assigning every criterion `1.0`, citing only `not-a-real-locator`, and supplying every hard gate as raw `true` produces a perfect 100/100 pass. No artifact is required. The scorer therefore remains circular and gameable.

The acceptance threshold also regresses. The preserved report rubric passes at 76/90 (84.444%), Optimizer A permits 84%, and the in-progress scorer uses 75%. A new denominator cannot be used to lower the existing report gate.

## Blocking issues

1. Evidence strings are checked for presence, not resolution, relevance, path confinement, artifact hash, or selector existence.
2. Hard gates accept unproven booleans instead of independent, versioned evaluator receipts.
3. A public catalog entry tagged `held_out` exposes its mutations and expected outcomes. It is a development case, not a held-out test.
4. Symbolic fixture labels and catalog entries do not prove a target evaluator, fixture, oracle, or artifact was executed.
5. Continuous subjective scores have no intermediate behavioral anchors, calibration, agreement target, or adjudication.
6. The same generic locator can back all twenty criteria, letting one narrow receipt inflate every dimension.
7. The legacy crosswalk maps names but does not prove semantic equivalence for output completeness, chart alignment, Unicode, page numbering, or source-labelled slides.
8. Iterations cannot be compared without matching catalog, rubric, fixture, evaluator, renderer/environment, seed, route, and artifact fingerprints.
9. Repeatability lacks a minimum repeat count and semantic tolerance policy.
10. Executive-suitability and readability ratings need plausible keyword-rich negative controls.
11. Render QA needs every page/slide/dashboard page, multiple viewports, and key filter states, not only a first-view receipt.
12. Optimizer file ownership and held-out immutability are not technically enforced.
13. No paired acceptance rule prevents aggregate gains from hiding per-route, per-agent, or per-dimension regressions.

The complete evidence, exploit descriptions, impacts, fixes, and acceptance tests are in `optimizer_b_critique.json`.

## Round 2 acceptance boundary

Round 2 must preserve every existing hard gate and keep the report route at or above 76/90. It must add typed hash-bound evidence locators, independent gate receipts, content-addressed fixtures, a genuinely frozen held-out boundary, registry completeness, explicit score anchors, full rendered-output coverage, comparable iteration fingerprints, three-run stability checks, optimizer diff controls, and paired no-regression acceptance.

The legacy report rubric should continue running in parallel until mutation tests prove criterion-by-criterion equivalence. Missing or skipped cases remain failures. A higher aggregate score with any new hard-gate, route, case, or required-dimension regression must be rejected.

## Handoff

Optimizer A should respond in a new pass-2 artifact and close each of the 15 findings with implementation evidence and a focused negative test. The pass-1 proposal and this critique are historical audit records and should not be rewritten.
