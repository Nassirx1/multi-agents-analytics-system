# debug-analysis-code

Use this skill when analysis code needs debugging, technical inspection, or logical verification, including cases where the script runs but the results may still be wrong.

## Goal
Catch runtime failures, silent logic bugs, data-contract mismatches, and methodology issues that would weaken the reviewer, report, or decision output.

## Inspect In Order
1. Reproduce or localize the failure, suspicious output, or questionable chart.
2. Verify imports, paths, file loading, serialization, and export behavior.
3. Check dataframe contracts: required columns, dtypes, null handling, row counts, and sort order.
4. Inspect joins, filters, groupings, aggregations, and feature/target usage for silent mistakes.
5. Verify metrics, comparisons, and chart logic against small spot checks from the raw data.
6. Check for data leakage, broken assumptions, invalid baselines, and misleading transformations.

## Common Issues To Look For
- import or package mismatches
- variable reuse or overwritten intermediate state
- incorrect file paths or output paths
- dataframe and column mismatches
- wrong joins, filters, groupings, or aggregations
- wrong target or feature selection
- silent metric errors or misleading summary logic
- invalid time ordering or comparison windows
- plotting logic that mismatches the underlying data
- serialization or export failures

## Relationship To Review
Use this skill to surface technical and logical issues that support the code reviewer, including hidden problems that do not appear as exceptions.

## Required Output
For each issue, report:

- `Type`: runtime, logic, or methodology
- `Issue`: what is wrong
- `Why it matters`: why it is a real problem
- `Effect on results`: how outputs or conclusions are impacted
- `Smallest safe fix`: the minimal change that corrects the problem

Prefer the smallest safe fix over broad rewrites unless the design is fundamentally broken.
