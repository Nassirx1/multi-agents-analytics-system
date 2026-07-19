# Root Integration Audit

## Completion status

The four Codex subagent workstreams completed: benchmark architecture, deterministic workflow execution, Optimizer A assurance hardening, and Optimizer B adversarial verification. These are repository-development roles and are not runtime workflow agents.

## Verified benchmark state

- Frozen catalog: 24 benchmark cases and 30 route executions.
- Catalog SHA-256: `9f37a48937b3d2b739d4be0a93135f03a9b080592cf5bde5190b5e2fb280bb23`.
- Semantic result fingerprint: `71d21670300e81a80558dd7f98e21ded984272d3edb336a4efaeb3a45c0a27b6`.
- Pilot: 29 pass, 1 fail, 0 skip.
- Infrastructure regression suite: passed.
- Full repository regression suite: passed.

## Preserved workflow finding

`maa.agent.actionable_bounded_recommendations.v1` fails because the benchmark's canonical recommendation contract requires a `metric` field while the current Decision Maker schema emits `validation_metric`. This remains a visible contract decision for future improvement; the benchmark was not weakened to manufacture a pass.

During integration, the evaluator was corrected so this field-name assertion no longer invents unrelated failures for `unsupported_claims_absent` or `high_stakes_safety_clear`. The current receipt reports only `required_action_fields: deterministic assertion failed`. This supersedes the earlier hard-gate wording in Optimizer B's historical final review while preserving its accepted assurance verdict.

## Planning artifacts

- Canonical JSON receipt: `evals/benchmark_pilot/benchmark_results.json`.
- Human-readable receipt: `evals/benchmark_pilot/benchmark_results.md`.
- Excel plan: `evals/benchmark_pilot/benchmark_results.xlsx`.
- Excel sheets: Summary, Benchmark Results, Improvement Plan, Catalog Coverage.
