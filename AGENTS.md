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

## Self-Evolution Engineer Rules
- When asked to evolve this workflow, Codex acts as the self-evolution engineer.
- Run relevant tests before and after candidate modifications.
- Prefer improving skills and prompts before changing core code.
- Change core code when a real bug or design issue is identified.
- Never edit `.env`, hardcode secrets, or expose credentials in logs, reports, diffs, or summaries.
- Never run an infinite improvement loop; obey the configured max evolution iteration limit.
- Always produce a diff summary and explain why each accepted change was made.

## Reporting Rules
- PDF and slide outputs must reflect the full workflow, not only the final analysis code.
- Reports and slides should be decision-oriented, evidence-backed, and readable by business stakeholders.
- Slide generation must remain runnable from the normal Python workflow without Codex, MCP, browser automation, PowerPoint add-ins, or remote repository access at runtime.
- The slide story layer should produce structured deck specs, not raw PowerPoint coordinates.
- The renderer must remain deterministic and style-controlled: it owns margins, spacing, typography, colors, chart styling, and alignment.
- New slide templates should be simple, clean, consulting-style layout blueprints, not finished static slides.
- Prefer code-saved figure images for EDA slides so slide visuals match the PDF visuals; structured analysis artifacts / chart specs are the fallback slide visual source when figure files are unavailable.
- EDA findings should briefly explain the code visuals, not replace them. Analysis slides should be visual-first whenever code figures or structured chart data exist.
- Do not add heavy slide or visualization dependencies without a clear runtime justification.
- Preserve backward compatibility for existing workflow outputs, the PDF report generator, and the code reviewer loop.
