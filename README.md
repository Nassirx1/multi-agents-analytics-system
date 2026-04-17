# Multi agents analytics system

Multi agents analytics system is a multi-agent analytics workflow that turns user-provided datasets into analytical code, visualizations, business interpretation, decision recommendations, a professional PDF report, and a professional slide deck.

## Workflow

The application runs a 9-step workflow:

1. Data Understander
2. Market Researcher
3. Analysis Planner
4. Data Scientist Coder
5. Code Reviewer
6. Business Translator
7. Decision Maker
8. PDF Report Generator
9. Slide Deck Generator

The workflow includes a coder-reviewer loop so analysis code is checked for technical quality, analytical fit, business usefulness, and visual output quality before final reporting.

## Runtime inputs

Each run prompts in the terminal for:

- OpenRouter API key
- Brave Search API key
- optional model override
- dataset file path or folder path containing CSV files
- optional dataset or business description from the user

If the model override is left blank, the workflow uses `deepseek/deepseek-v3.2`.

API keys are used in memory only for the current process. They are not written to `.env`, config files, or repository state.

## Installation

Use Python 3.11+.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

If you prefer not to activate the virtual environment:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Running the workflow

Windows one-click runner:

```powershell
run_workflow.cmd
```

Direct Python entrypoint:

```powershell
python -m analytics_workflow
```

The launcher will:

- ask for credentials
- ask for a dataset path or folder
- ask for optional user context about the dataset or business problem
- display step-by-step progress from step 1 to step 9
- generate timestamped figures as needed
- generate PDF and PPTX outputs with safe timestamped fallbacks when old files are locked

## Outputs

Typical run outputs include:

- timestamped figure files such as `figure_1_YYYYMMDD_HHMMSS.png`
- a PDF report such as `analytics_report.pdf` or a timestamped fallback if the target file is locked
- a PPTX deck such as `analytics_report.pptx` or a timestamped fallback if the target file is locked
- a run log such as `analytics_run_YYYYMMDD_HHMMSS.log`

Generated outputs are intentionally ignored by Git and are not part of the initial published repository surface.

## Tests

Run the unit test suite with:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -q
```

## Repository contents

The repository intentionally focuses on the application surface:

- `analytics_workflow/` for runtime, orchestration, agents, clients, and reporting
- `tests/` for regression coverage
- `run_workflow.cmd` for Windows launching
- `AGENTS.md` and `.codex/skills/` for project-level agent guidance and reusable support workflows

Local datasets, generated reports, figures, logs, and virtual environment files are excluded from source control by default.
