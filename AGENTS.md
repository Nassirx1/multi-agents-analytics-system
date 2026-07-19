# Project Agents

## Purpose
This repository is a multi-agent analytics system that transforms uploaded datasets into one explicitly selected output: a decision-oriented PDF/PowerPoint report or a self-contained interactive HTML dashboard.

## Runtime Priorities
1. Ask for the output path before loading credentials, datasets, or agents.
2. Keep the Analytics Report and HTML Dashboard paths isolated after selection.
3. Prefer accurate, source-backed results over visual or narrative polish.
4. Preserve readability, visibility, trustworthiness, and executive suitability.
5. Fail clearly when validation cannot establish that an artifact is complete and usable.

## Core Workflow
### Analytics Report Path
1. Data Understander
2. Market Researcher
3. Analysis Planner
4. Data Scientist Coder
5. Code Reviewer
6. Business Translator
7. Decision Maker
8. PDF Report Generator
9. Slide Deck Generator

### HTML Dashboard Path
1. Data Understander
2. Dashboard Planning Agent
3. HTML Dashboard Generator
4. HTML Dashboard QA Agent

## Engineering Rules
- Inspect the repository structure before major changes.
- Prefer small, composable modules and explicit contracts between workflow steps.
- Keep prompts and templates separate from orchestration logic when practical.
- Avoid hidden coupling between agents or report/export stages.
- Never hardcode, log, or commit secrets. Load required credentials from the process, user, or machine environment, with the git-ignored local `.env` as a fallback. Never prompt the user to type credentials.
- Preserve reproducibility through stable inputs, deterministic checks, and traceable artifact paths.
- Log important workflow steps without exposing secrets.
- Save intermediate artifacts when they help debugging, review, reporting, or export validation.
- Keep Power BI MCP and authoring skills available only as Codex tooling; the Python runtime dashboard path must not import or invoke them.
- Keep benchmark suites, recursive evaluation records, test fixtures, and internal QA utilities out of the public repository.

## Validation Rules
- After meaningful code changes, run the most relevant tests or smoke checks available.
- If analysis, report, or export code changes, verify the artifact generation path end to end.
- Prefer deterministic validation over subjective inspection when possible.
- Do not report an HTML dashboard as complete until its deterministic QA receipt passes.

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

## HTML Dashboard Rules
- The dashboard path must never invoke PDF, PowerPoint, PBIP, PBIR, Power BI Desktop, or Power BI runtime generation.
- Produce one self-contained HTML file with no required network, CDN, font, image, or script dependency.
- Validate every planned dataset and field before rendering.
- Use no more than two charts per page; add pages instead of crowding visuals.
- Global filters must recalculate KPIs, charts, and detail tables from the embedded source rows.
- Render explicit no-data states and reject blank, malformed, clipped, or overlapping visuals.
- Keep charts source-backed and choose chart types that match the field types.
