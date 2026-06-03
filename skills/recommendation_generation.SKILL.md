# Recommendation Generation Skill

## Purpose
Turn data evidence and business interpretation into ranked recommendations for stakeholders.

## Inputs
- Workflow outputs, analysis results, business translation, market context, and limitations.

## Expected Outputs
- Decision context, ranked actions, rationale, evidence, timeline, impact, limitations, and final recommendation.

## Hard Rules
- Every recommendation must state what to do, why, and what evidence supports it.
- Keep limitations and validation steps visible.
- Do not recommend work unsupported by the data as if it were proven.
- Include owner, target segment, timeline, validation metric, stop/go trigger, and guardrail when the output feeds reports or slides.
- For HR or other sensitive domains, frame actions as support, review, validation, and fairness checks; do not imply automated adverse decisions.

## Failure Cases To Avoid
- Generic "monitor metrics" recommendations with no trigger or owner-facing rationale.
- Hiding material uncertainty.
- Recommendations that conflict with the stated objective.
- Repeating boilerplate such as "focused pilot and explicit success criteria" without naming the actual metric or decision checkpoint.

## Quality Checklist
- Recommendations are ranked and actionable.
- Evidence connects analysis and market/business context.
- The report and slide deck can carry the output without rewriting it.
- A stakeholder can tell who acts, where, by when, how success is measured, and what guardrail prevents misuse.

## Example
Recommend a pilot for the highest-risk segment with a timeline and success metric instead of a broad company-wide intervention.
