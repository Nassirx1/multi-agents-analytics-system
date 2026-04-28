from __future__ import annotations

import logging
import os
import re
import traceback
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.figure import Figure

from .agents import (
    BaseAgent,
    BusinessInsightsTranslatorAgent,
    DataScientistCoderAgent,
    DataScientistReviewerAgent,
    DataUnderstanderAgent,
    DecisionMakerAgent,
    MarketResearcherAgent,
    PlannerAgent,
    PresentationArchitectAgent,
)
from .clients import (
    BraveSearchClient,
    CostTracker,
    OpenRouterClient,
    SYSTEM_LOGGER,
    SharedContextStore,
    setup_logging,
)
from .reporting import (
    PPTX_AVAILABLE,
    REPORTLAB_AVAILABLE,
    _expand_slides_with_visuals,
    _format_analysis_findings,
    _format_dataset_overview,
    _format_market_findings,
    _format_recommendations,
    _market_claim_pairs,
    _preferred_output_path,
    _safe_paragraph,
    _source_index_map,
    _stringify,
    generate_pdf_report,
    generate_slide_deck,
)
from .runtime_config import RuntimeConfig
from .serialization import json_dumps_safe, make_json_safe

warnings.filterwarnings("ignore")
plt.style.use("seaborn-v0_8")
sns.set_palette("husl")

WORKFLOW_STEPS = [
    "Data Understander",
    "Market Researcher",
    "Analysis Planner",
    "Data Scientist Coder",
    "Code Reviewer",
    "Business Translator",
    "Decision Maker",
    "PDF Report Generator",
    "Slide Deck Generator",
]

StepCallback = Callable[[int, str, str], None]


class MultiAgentOrchestrator:
    def __init__(self, config: RuntimeConfig, step_callback: StepCallback | None = None) -> None:
        self.openrouter_client = OpenRouterClient(config.openrouter_api_key, config.model_name)
        self.brave_client = BraveSearchClient(config.brave_search_api_key) if config.brave_search_api_key else None
        self.shared_store = SharedContextStore()
        self.step_callback = step_callback
        kwargs = {"openrouter_client": self.openrouter_client, "shared_store": self.shared_store}
        self.agents: dict[str, BaseAgent] = {
            "data_understander": DataUnderstanderAgent("Data Understander", "Senior Data Analyst", "data profiling", **kwargs),
            "market_researcher": MarketResearcherAgent("Market Researcher", "Market Research Specialist", "market trends", brave_client=self.brave_client, **kwargs),
            "planner": PlannerAgent("Analysis Planner", "Senior Data Strategist", "analysis planning", **kwargs),
            "coder": DataScientistCoderAgent("Data Scientist Coder", "Senior Data Scientist", "python analytics", **kwargs),
            "reviewer": DataScientistReviewerAgent("Code Reviewer", "Senior Reviewer", "debugging and analytical review", **kwargs),
            "business_translator": BusinessInsightsTranslatorAgent("Business Translator", "Business Intelligence Expert", "executive translation", **kwargs),
            "decision_maker": DecisionMakerAgent("Decision Maker", "Senior Business Analyst", "decision recommendations", **kwargs),
            "presentation_architect": PresentationArchitectAgent("Presentation Architect", "Presentation Consultant", "slide storytelling", **kwargs),
        }
        self.workflow_state: dict[str, Any] = {
            "csv_data": {},
            "user_data_description": "",
            "agent_outputs": {},
            "analysis_results": {},
            "analysis_artifact_warnings": [],
            "current_step": 0,
            "total_steps": 9,
            "status": "initialized",
            "saved_figures": [],
            "generated_reports": {},
        }
        self._logger = logging.getLogger("Orchestrator")

    def set_user_data_description(self, description: str) -> None:
        normalized = description.strip()
        self.workflow_state["user_data_description"] = normalized
        for agent in self.agents.values():
            agent.set_shared_context(user_data_description=normalized)

    def load_csv_paths(self, csv_paths: list[Path]) -> bool:
        try:
            for path in csv_paths:
                self.workflow_state["csv_data"][path.name] = pd.read_csv(path)
            return True
        except Exception as exc:
            self._logger.error("CSV load error: %s", exc)
            return False

    def _set_step(self, step_number: int, status: str) -> None:
        self.workflow_state["current_step"] = step_number
        step_name = WORKFLOW_STEPS[step_number - 1]
        if self.step_callback:
            self.step_callback(step_number, step_name, status)

    def execute_workflow(self) -> dict[str, Any]:
        outputs = self.workflow_state["agent_outputs"]
        self.workflow_state["status"] = "running"
        try:
            self._set_step(1, "running")
            data_insights = self.agents["data_understander"].execute(self.workflow_state["csv_data"])
            outputs["data_understander"] = data_insights
            self._set_step(1, "done")

            self._set_step(2, "running")
            market_insights = self.agents["market_researcher"].execute(data_insights)
            outputs["market_researcher"] = market_insights
            self._set_step(2, "done")

            self._set_step(3, "running")
            analysis_plan = self.agents["planner"].execute(data_insights, market_insights)
            outputs["planner"] = analysis_plan
            self._set_step(3, "done")

            final_code = self._coding_loop(analysis_plan)
            outputs["final_code"] = final_code
            if self.workflow_state["analysis_results"].get("execution_status") != "success":
                self.workflow_state["analysis_results"] = self._execute_code(final_code)
            final_artifact_issues = self._analysis_output_issues(self.workflow_state["analysis_results"])
            self.workflow_state["analysis_artifact_warnings"] = final_artifact_issues
            if self.workflow_state["analysis_results"].get("execution_status") != "success":
                execution_error = self.workflow_state["analysis_results"].get("error", "Unknown execution error.")
                raise RuntimeError(f"Final analysis code failed to execute: {execution_error}")

            self._set_step(6, "running")
            business = self.agents["business_translator"].execute(
                self.workflow_state["analysis_results"],
                data_insights,
                market_insights,
            )
            outputs["business_translator"] = business
            self._set_step(6, "done")

            self._set_step(7, "running")
            decision = self.agents["decision_maker"].execute(outputs, self.workflow_state["analysis_results"], business)
            outputs["decision_maker"] = decision
            self._set_step(7, "done")

            outputs["presentation_architect"] = self.agents["presentation_architect"].execute(self.workflow_state)

            self._set_step(8, "running")
            self.workflow_state["generated_reports"]["pdf"] = generate_pdf_report(self.workflow_state)
            self._set_step(8, "done")

            self._set_step(9, "running")
            self.workflow_state["generated_reports"]["slide_deck"] = generate_slide_deck(self.workflow_state)
            self._set_step(9, "done")

            self.workflow_state["status"] = "completed"
        except Exception as exc:
            self._logger.error("Workflow failed: %s", exc)
            self._logger.error(traceback.format_exc())
            self.workflow_state["status"] = "error"
        return self.workflow_state

    def _coding_loop(self, analysis_plan: dict[str, Any], max_iterations: int = 4) -> str:
        current_code = ""
        approved_code = ""
        best_runnable_code = ""
        last_execution_error = "No execution attempt was completed."
        for iteration in range(max_iterations):
            self._set_step(4, f"running (iteration {iteration + 1})")
            try:
                current_code = self.agents["coder"].execute(analysis_plan, self.workflow_state["csv_data"], iteration + 1)
            except Exception as exc:
                last_execution_error = str(exc)
                self.agents["coder"].context["review_feedback"] = (
                    "The previous response was not a valid analysis script. "
                    "Return executable Python only with analysis_summary, business_findings, and figure_captions.\n"
                    f"Error: {exc}"
                )
                self._set_step(4, "revise")
                continue
            self._set_step(4, "done")

            self._set_step(5, f"running (iteration {iteration + 1})")
            execution = self._execute_code(current_code)
            artifact_issues = self._analysis_output_issues(execution)
            if execution.get("execution_status") != "success":
                last_execution_error = execution.get("error", "Unknown execution error.")
            if execution.get("execution_status") == "success":
                best_runnable_code = current_code
                self.workflow_state["analysis_results"] = execution
                self.workflow_state["saved_figures"] = execution.get("figures_generated", [])
                self.workflow_state["analysis_artifact_warnings"] = artifact_issues

                if not artifact_issues:
                    approved_code = current_code
                    self._set_step(5, "done")
                    break

                review = self.agents["reviewer"].execute(
                    current_code,
                    analysis_plan,
                    iteration + 1,
                    execution=execution,
                    artifact_issues=artifact_issues,
                )
                self.workflow_state["agent_outputs"][f"reviewer_iter_{iteration + 1}"] = review
                decision = review.get("decision", "").upper()
                review_malformed = decision not in {"APPROVE", "REVISE", "REJECT"}
                if review_malformed:
                    self._logger.warning(
                        "Reviewer returned malformed review (decision=%r, parse_error=%r). Treating as warning-only.",
                        review.get("decision"),
                        review.get("parse_error"),
                    )
                self.agents["coder"].context["review_feedback"] = json_dumps_safe(
                    {
                        "execution": execution,
                        "artifact_issues": artifact_issues,
                        "review": review,
                    },
                    indent=2,
                )
                last_execution_error = " ; ".join(artifact_issues)
                if iteration < max_iterations - 1:
                    self._set_step(5, "revise")
                    continue
                self._set_step(5, "done (best effort)")
                break

            if execution.get("execution_status") != "success":
                feedback_payload = json_dumps_safe(
                    {
                        "execution": execution,
                        "artifact_issues": artifact_issues,
                        "review": {
                            "decision": "REVISE",
                            "summary": "The generated output was not runnable Python. Fix syntax and return executable code only.",
                        },
                    },
                    indent=2,
                )
                missing_module = execution.get("missing_module")
                if missing_module:
                    dependency_review = {
                        "decision": "REVISE",
                        "critical_issues": [
                            f"Runtime failed because Python could not import '{missing_module}'."
                        ],
                        "improvements": [
                            (
                                f"If '{missing_module}' is genuinely needed, add a guarded runtime bootstrap using "
                                "importlib, subprocess, and sys so the script installs the package with "
                                f"`sys.executable -m pip install {missing_module}` before importing it."
                            ),
                            (
                                f"If the package was only forgotten in the import section, add the explicit "
                                f"`import {missing_module}` or equivalent package import and keep the script runnable."
                            ),
                        ],
                        "summary": (
                            f"Revise the analysis code so missing dependency '{missing_module}' is handled automatically "
                            "instead of crashing the run."
                        ),
                    }
                    self.workflow_state["agent_outputs"][f"reviewer_iter_{iteration + 1}"] = dependency_review
                    feedback_payload = json_dumps_safe(
                        {
                            "execution": execution,
                            "artifact_issues": artifact_issues,
                            "review": dependency_review,
                        },
                        indent=2,
                    )
                self.agents["coder"].context["review_feedback"] = feedback_payload
                self._set_step(5, "revise")
                continue
        if approved_code or best_runnable_code:
            self._set_step(5, "done (best effort)")
            return approved_code or best_runnable_code
        self._set_step(5, "failed")
        raise RuntimeError(
            f"No runnable analysis code was generated after {max_iterations} attempts. "
            f"Last execution error: {last_execution_error}"
        )

    def _analysis_output_issues(self, execution: dict[str, Any]) -> list[str]:
        issues: list[str] = []
        if execution.get("execution_status") != "success":
            issues.append("Analysis code did not execute successfully.")
            return issues

        figures = execution.get("figures_generated", []) or []
        analysis_summary = execution.get("analysis_summary", {}) or {}
        figure_captions = execution.get("figure_captions", {}) or {}
        business_findings = execution.get("business_findings", []) or []

        if len(figures) < 3:
            issues.append("Analysis produced fewer than 3 saved figures, so visual analysis is too thin.")
        if not isinstance(analysis_summary, dict) or len(analysis_summary) < 2:
            issues.append("analysis_summary is missing or too small to support business reporting.")
        elif not self._has_numeric_evidence(analysis_summary):
            issues.append("analysis_summary does not contain clear numeric evidence.")
        if not isinstance(business_findings, list) or len(business_findings) < 2:
            issues.append("business_findings are missing or too small to support business reporting.")

        missing_captions = [figure for figure in figures if not _stringify(figure_captions.get(figure, "")).strip()]
        if missing_captions:
            issues.append(f"Missing figure captions for: {', '.join(missing_captions[:4])}.")

        return issues

    def _has_numeric_evidence(self, value: Any) -> bool:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if isinstance(value, str):
            return bool(re.search(r"\d", value))
        if isinstance(value, dict):
            return any(self._has_numeric_evidence(item) for item in value.values())
        if isinstance(value, list):
            return any(self._has_numeric_evidence(item) for item in value)
        return False

    def _normalize_business_findings(
        self,
        raw_business_findings: Any,
        analysis_summary: Any,
        figure_captions: Any,
    ) -> list[str]:
        findings: list[str] = []
        seen: set[str] = set()

        def add_finding(value: Any) -> None:
            clean = _stringify(value).strip()
            if not clean or clean in seen:
                return
            seen.add(clean)
            findings.append(clean)

        if isinstance(raw_business_findings, str):
            add_finding(raw_business_findings)
        elif isinstance(raw_business_findings, list):
            for item in raw_business_findings:
                add_finding(item)

        if findings:
            return findings

        if isinstance(analysis_summary, dict):
            for key, value in list(analysis_summary.items())[:4]:
                add_finding(f"{str(key).replace('_', ' ').title()}: {_stringify(value)}")
        if isinstance(figure_captions, dict):
            for caption in list(figure_captions.values())[:3]:
                add_finding(caption)
        return findings

    def _execute_code(self, code: str) -> dict[str, Any]:
        original_pyplot_savefig = plt.savefig
        original_figure_savefig = Figure.savefig
        try:
            exec_globals: dict[str, Any] = {
                "pd": pd,
                "np": np,
                "plt": plt,
                "sns": sns,
                "nan": np.nan,
                "NaN": np.nan,
                "inf": np.inf,
                "Infinity": np.inf,
                "null": None,
                "NULL": None,
                "true": True,
                "false": False,
                "__builtins__": __builtins__,
            }
            run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            saved_figure_paths: list[str] = []
            figure_name_map: dict[str, str] = {}

            def resolve_figure_path(filename: Any) -> Any:
                if not isinstance(filename, (str, Path)):
                    return filename
                raw_path = Path(filename)
                raw_name = str(raw_path)
                if raw_path.suffix.lower() != ".png":
                    return filename
                if not re.fullmatch(r"figure_\d+", raw_path.stem):
                    return filename
                resolved_path = raw_path.with_name(f"{raw_path.stem}_{run_stamp}{raw_path.suffix}")
                figure_name_map[raw_name] = str(resolved_path)
                return str(resolved_path)

            def tracked_pyplot_savefig(*args: Any, **kwargs: Any) -> Any:
                if args:
                    resolved = resolve_figure_path(args[0])
                    args = (resolved, *args[1:])
                    if isinstance(resolved, str) and resolved not in saved_figure_paths:
                        saved_figure_paths.append(resolved)
                elif "fname" in kwargs:
                    resolved = resolve_figure_path(kwargs["fname"])
                    kwargs["fname"] = resolved
                    if isinstance(resolved, str) and resolved not in saved_figure_paths:
                        saved_figure_paths.append(resolved)
                return original_pyplot_savefig(*args, **kwargs)

            def tracked_figure_savefig(self_figure: Figure, *args: Any, **kwargs: Any) -> Any:
                if args:
                    resolved = resolve_figure_path(args[0])
                    args = (resolved, *args[1:])
                    if isinstance(resolved, str) and resolved not in saved_figure_paths:
                        saved_figure_paths.append(resolved)
                elif "fname" in kwargs:
                    resolved = resolve_figure_path(kwargs["fname"])
                    kwargs["fname"] = resolved
                    if isinstance(resolved, str) and resolved not in saved_figure_paths:
                        saved_figure_paths.append(resolved)
                return original_figure_savefig(self_figure, *args, **kwargs)

            plt.savefig = tracked_pyplot_savefig
            Figure.savefig = tracked_figure_savefig
            loaded_dataframes: list[pd.DataFrame] = []
            for name, df in self.workflow_state["csv_data"].items():
                clean = re.sub(r"[^a-zA-Z0-9_]", "_", name.replace(".csv", ""))
                df_copy = df.copy(deep=True)
                loaded_dataframes.append(df_copy)
                exec_globals[f"df_{clean}"] = df_copy
                exec_globals[f"df_{clean}_numeric"] = df_copy.select_dtypes(include=[np.number])
                exec_globals[f"df_{clean}_categorical"] = df_copy.select_dtypes(include=["object", "string", "category"])
            if len(loaded_dataframes) == 1:
                exec_globals["df"] = loaded_dataframes[0]
                exec_globals["df_numeric"] = loaded_dataframes[0].select_dtypes(include=[np.number])
                exec_globals["df_categorical"] = loaded_dataframes[0].select_dtypes(include=["object", "string", "category"])
            exec(code, exec_globals)
            figures = [path for path in saved_figure_paths if os.path.exists(path)]
            self.workflow_state["saved_figures"] = figures
            raw_figure_captions = exec_globals.get("figure_captions", {})
            figure_captions = {}
            if isinstance(raw_figure_captions, dict):
                for key, value in raw_figure_captions.items():
                    mapped_key = figure_name_map.get(str(key), str(key))
                    figure_captions[mapped_key] = make_json_safe(value)
            analysis_summary = make_json_safe(exec_globals.get("analysis_summary", {}))
            business_findings = self._normalize_business_findings(
                make_json_safe(exec_globals.get("business_findings", [])),
                analysis_summary,
                figure_captions,
            )
            contract_issues = self._missing_analysis_contract_items(
                analysis_summary,
                business_findings,
                figure_captions,
            )
            if contract_issues:
                return {
                    "execution_status": "failed",
                    "error": (
                        "Analysis script did not produce the required outputs: "
                        + ", ".join(contract_issues)
                    ),
                    "traceback": "",
                    "analysis_summary": analysis_summary,
                    "business_findings": business_findings,
                    "figure_captions": figure_captions,
                    "figures_generated": figures,
                }
            return {
                "execution_status": "success",
                "figures_generated": figures,
                "analysis_summary": analysis_summary,
                "business_findings": business_findings,
                "figure_captions": figure_captions,
            }
        except ModuleNotFoundError as exc:
            missing = getattr(exc, "name", None)
            if not missing:
                match = re.search(r"No module named ['\"]([^'\"]+)['\"]", str(exc))
                missing = match.group(1) if match else ""
            return {
                "execution_status": "failed",
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "missing_module": missing,
            }
        except Exception as exc:
            return {"execution_status": "failed", "error": str(exc), "traceback": traceback.format_exc()}
        finally:
            plt.savefig = original_pyplot_savefig
            Figure.savefig = original_figure_savefig

    def _missing_analysis_contract_items(
        self,
        analysis_summary: Any,
        business_findings: Any,
        figure_captions: Any,
    ) -> list[str]:
        missing: list[str] = []
        if not isinstance(analysis_summary, dict) or not analysis_summary:
            missing.append("analysis_summary")
        if not isinstance(business_findings, list) or not business_findings:
            missing.append("business_findings")
        if not isinstance(figure_captions, dict):
            missing.append("figure_captions")
        return missing


def find_csv_files(workspace: Path) -> list[Path]:
    return sorted(workspace.glob("*.csv"))


def resolve_dataset_paths(dataset_input: str, workspace: Path) -> list[Path]:
    normalized = dataset_input.strip().strip('"')
    if not normalized:
        return find_csv_files(workspace)

    candidate = Path(normalized)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    candidate = candidate.expanduser()

    if candidate.is_file():
        if candidate.suffix.lower() != ".csv":
            raise ValueError("The selected file must be a .csv file.")
        return [candidate]

    if candidate.is_dir():
        csv_files = sorted(candidate.glob("*.csv"))
        if not csv_files:
            raise FileNotFoundError("The selected folder does not contain any CSV files.")
        return csv_files

    raise FileNotFoundError("The provided dataset path does not exist.")


def prompt_dataset_paths(workspace: Path, input_fn: Callable[[str], str] = input) -> list[Path]:
    while True:
        dataset_input = input_fn(
            "CSV file path or folder path (press Enter to use CSV files in the current workspace): "
        )
        try:
            csv_files = resolve_dataset_paths(dataset_input, workspace)
        except (FileNotFoundError, ValueError) as exc:
            print(str(exc))
            continue
        if not csv_files:
            print("No CSV files were found. Please enter a valid CSV file or folder path.")
            continue
        return csv_files


def prompt_user_data_description(input_fn: Callable[[str], str] = input) -> str:
    return input_fn(
        "Describe the dataset, business problem, or analysis goal (optional but recommended): "
    ).strip()


def run_terminal_workflow(config: RuntimeConfig, workspace: Path | None = None) -> int:
    root = workspace or Path.cwd()
    print()
    print("Analytics workflow launcher")
    print("===========================")
    print(f"Workspace: {root}")
    print(f"Runtime module: {Path(__file__).resolve()}")
    csv_files = prompt_dataset_paths(root)
    print("Datasets:")
    for path in csv_files:
        print(f"  - {path.name}")

    orchestrator = MultiAgentOrchestrator(config, step_callback=_print_step_update)
    user_data_description = prompt_user_data_description()
    orchestrator.set_user_data_description(user_data_description)
    if user_data_description:
        print("User context:")
        print(f"  {user_data_description}")
    if not orchestrator.load_csv_paths(csv_files):
        return 1
    result = orchestrator.execute_workflow()
    print()
    print(f"Workflow status: {result.get('status')}")
    for name, path in result.get("generated_reports", {}).items():
        print(f"Generated {name}: {path}")
    print()
    print(orchestrator.openrouter_client.cost_tracker.report())
    return 0 if result.get("status") == "completed" else 1


def _print_step_update(step_number: int, step_name: str, status: str) -> None:
    print(f"[{status}] Step {step_number}/9: {step_name}")
