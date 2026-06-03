# Analysis Planning Skill

## Purpose
Turn data readiness and market context into a focused analysis plan that the code generator can execute.

## Inputs
- Data profile, market research, and user objective.

## Expected Outputs
- Objectives, hypotheses, cleaning plan, role strategy, methods, visual plan, guardrails, and success metrics.

## Hard Rules
- Select methods that fit column roles, sample size, granularity, and the decision question.
- Say what to avoid when the data cannot support a tempting method.
- Prioritize evidence that can flow into reports, slides, and recommendations.

## Failure Cases To Avoid
- A generic EDA checklist with no decision focus.
- Causal or predictive plans without target, time, treatment, or leakage controls.
- Visual plans that do not state what comparison or trend matters.

## Quality Checklist
- The plan is executable by the coder from available inputs.
- Each method has a business reason.
- Cleaning and validation work precedes modeling or visualization.

## Example
With a region field and revenue metric, plan ranked segment comparisons and dispersion checks before asking for a driver model.
