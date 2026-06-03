# Code Review Skill

## Purpose
Review generated analysis code for runtime safety, analytical fit, output quality, and downstream usefulness.

## Inputs
- Analysis plan, data-understanding output, code, execution result, artifact warnings, and user objective.

## Expected Outputs
- Structured review decision, critical issues, improvements, summary, and quality score.

## Hard Rules
- Do not approve code only because it runs.
- Reject unsafe imports, unsupported dependencies, weak evidence, and output contract violations.
- Reject random filler visuals that do not follow data-understanding roles, targets, dates, segments, or numeric drivers.
- Use execution failures and artifact warnings as review evidence.

## Failure Cases To Avoid
- Missing a misleading chart or a spurious causal claim.
- Asking for broad rewrites when one concrete fix is enough.
- Ignoring downstream report and slide requirements.
- Ignoring the suited visual plan without documenting a data-based reason.

## Quality Checklist
- Review distinguishes correctness, analytical quality, and presentation readiness.
- Feedback is specific enough for the coder to revise.
- Approval means artifacts are usable by the rest of the workflow.

## Example
Request revision when code generates charts but omits captions and numeric business findings.
Request revision when code plots arbitrary columns instead of the identified target, time, segment, or numeric-driver pairings.
