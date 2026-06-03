# Data Profiling Skill

## Purpose
Profile uploaded CSV data so later agents know what the data can support before they plan analysis.

## Inputs
- CSV dataframes, column names, dtypes, row counts, sample values, and missingness summaries.
- The user-provided dataset description or decision question when available.

## Expected Outputs
- Dataset quality summary with shape, data types, missingness, duplicates, and notable outliers.
- Column role notes for identifiers, datetimes, numeric fields, categorical fields, binary fields, ordinal fields, and free text.
- Recommended analyses and analyses to avoid for each dataset.

## Hard Rules
- Treat user context as an objective lens, not as proof that a field exists.
- Keep identifiers out of chart and model candidates unless transformed for a clear reason.
- Distinguish data quality risk from business findings.
- Do not expose raw secrets or sensitive credential values in notes or logs.

## Failure Cases To Avoid
- Calling a timestamp or ID a numeric driver just because pandas parsed it as numeric.
- Recommending prediction, causality, or clustering without supporting columns and sample shape.
- Hiding severe missingness, duplicates, type-conversion risk, or thin samples.

## Quality Checklist
- The profile names usable roles for important columns.
- Data limitations are explicit and actionable.
- Recommended analyses follow from the profile and user objective.
- Later planner and coder agents can act without inventing columns.

## Example
For `customer_id`, `signup_date`, `segment`, and `churned`, classify the ID as an identifier, the date as time context, the segment as a comparison field, and `churned` as a possible binary outcome.
