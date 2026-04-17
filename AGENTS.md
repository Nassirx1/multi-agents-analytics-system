# Project Agents

## Purpose
This repository is a multi-agent analytics system that transforms uploaded datasets into analysis, business interpretation, decision recommendations, a professional PDF report, and a professional slide deck.

## Core Workflow
1. Data Understander
2. Market Researcher
3. Analysis Planner
4. Data Scientist Coder
5. Code Reviewer
6. Business Translator
7. Decision Maker
8. PDF Report Generator
9. Slide Deck Generator

## Engineering Rules
- Inspect the repository structure before major changes.
- Prefer small, composable modules and explicit contracts between workflow steps.
- Keep prompts and templates separate from orchestration logic when practical.
- Avoid hidden coupling between agents or report/export stages.
- Never hardcode or persist secrets; prompt for required API credentials on every run and keep them in memory only.
- Preserve reproducibility through stable inputs, deterministic checks, and traceable artifact paths.
- Log important workflow steps without exposing secrets.
- Save intermediate artifacts when they help debugging, review, reporting, or export validation.

## Validation Rules
- After meaningful code changes, run the most relevant tests or smoke checks available.
- If analysis, report, or export code changes, verify the artifact generation path end to end.
- Prefer deterministic validation over subjective inspection when possible.

## Reporting Rules
- PDF and slide outputs must reflect the full workflow, not only the final analysis code.
- Reports and slides should be decision-oriented, evidence-backed, and readable by business stakeholders.
