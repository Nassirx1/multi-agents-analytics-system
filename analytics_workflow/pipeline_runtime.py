from __future__ import annotations

import ast
import builtins
import importlib.util
import logging
import os
import re
import subprocess
import sys
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

BLOCKED_ANALYSIS_MODULES = {
    "subprocess",
    "requests",
    "socket",
    "shutil",
    "sys",
    "pip",
    "urllib",
    "http",
    "ftplib",
}

ALLOWED_ANALYSIS_IMPORT_ROOTS = {
    "collections",
    "datetime",
    "itertools",
    "json",
    "math",
    "matplotlib",
    "numpy",
    "pandas",
    "PIL",
    "plotly",
    "re",
    "scipy",
    "seaborn",
    "sklearn",
    "statistics",
    "statsmodels",
    "textwrap",
    "time",
    "warnings",
}

APPROVED_ANALYSIS_PACKAGE_INSTALLS = {
    "matplotlib": "matplotlib",
    "numpy": "numpy",
    "pandas": "pandas",
    "PIL": "pillow",
    "plotly": "plotly",
    "scipy": "scipy",
    "seaborn": "seaborn",
    "sklearn": "scikit-learn",
    "statsmodels": "statsmodels",
}

SAFE_BUILTINS = {
    name: getattr(builtins, name)
    for name in [
        "ArithmeticError",
        "AssertionError",
        "AttributeError",
        "Exception",
        "IndexError",
        "ImportError",
        "KeyError",
        "ModuleNotFoundError",
        "RuntimeError",
        "StopIteration",
        "TypeError",
        "ValueError",
        "__import__",
        "abs",
        "all",
        "any",
        "bool",
        "dict",
        "enumerate",
        "filter",
        "float",
        "getattr",
        "hasattr",
        "int",
        "isinstance",
        "len",
        "list",
        "locals",
        "map",
        "max",
        "min",
        "object",
        "print",
        "range",
        "round",
        "set",
        "setattr",
        "sorted",
        "str",
        "sum",
        "tuple",
        "zip",
    ]
}


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
            "workflow_objective": self._build_workflow_objective(""),
            "failure": {},
            "run_manifest": {
                "datasets": [],
                "figures": [],
                "reports": {},
                "warnings": [],
                "agent_outputs": [],
                "final_code_present": False,
            },
        }
        self._logger = logging.getLogger("Orchestrator")

    def set_user_data_description(self, description: str) -> None:
        normalized = description.strip()
        objective = self._build_workflow_objective(normalized)
        self.workflow_state["user_data_description"] = normalized
        self.workflow_state["workflow_objective"] = objective
        for agent in self.agents.values():
            agent.set_shared_context(user_data_description=normalized, workflow_objective=objective)

    def _build_workflow_objective(self, description: str) -> dict[str, Any]:
        normalized = description.strip()
        focus_terms = self._objective_focus_terms(normalized)
        kpi_hints = [
            term
            for term in focus_terms
            if term in {
                "revenue",
                "sales",
                "profit",
                "cost",
                "margin",
                "growth",
                "risk",
                "churn",
                "retention",
                "conversion",
                "price",
                "volume",
                "return",
            }
        ]
        decision_question = normalized or "Identify the most decision-useful patterns, risks, and opportunities in the uploaded dataset."
        return {
            "raw_description": normalized,
            "decision_question": decision_question,
            "audience": "business decision-makers",
            "focus_terms": focus_terms,
            "kpi_hints": kpi_hints,
            "acceptance_criteria": [
                "Analysis choices match the uploaded dataset structure.",
                "Findings include numeric evidence and clear business implications.",
                "Recommendations are tied to data analysis, market context, and the stated objective.",
                "PDF and slide outputs explain objective coverage and limitations.",
            ],
            "limitations": [],
        }

    def _objective_focus_terms(self, description: str) -> list[str]:
        stop_words = {
            "about",
            "analysis",
            "analyze",
            "data",
            "dataset",
            "file",
            "from",
            "into",
            "should",
            "that",
            "the",
            "this",
            "with",
        }
        terms = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", description.lower())
        focus_terms: list[str] = []
        for term in terms:
            if term in stop_words or term in focus_terms:
                continue
            focus_terms.append(term)
            if len(focus_terms) >= 12:
                break
        return focus_terms

    def load_csv_paths(self, csv_paths: list[Path]) -> bool:
        try:
            for path in csv_paths:
                key = path.name
                if key in self.workflow_state["csv_data"]:
                    key = f"{path.stem}_{abs(hash(str(path.resolve()))) % 100000}{path.suffix}"
                self.workflow_state["csv_data"][key] = pd.read_csv(path)
                self.workflow_state["run_manifest"]["datasets"].append(
                    {"name": key, "path": str(path), "rows": int(self.workflow_state["csv_data"][key].shape[0])}
                )
            return True
        except Exception as exc:
            self._logger.error("CSV load error: %s", exc)
            return False

    def _set_step(self, step_number: int, status: str) -> None:
        self.workflow_state["current_step"] = step_number
        step_name = WORKFLOW_STEPS[step_number - 1]
        if self.step_callback:
            self.step_callback(step_number, step_name, status)

    def _record_failure(self, exc: Exception) -> None:
        current_step = int(self.workflow_state.get("current_step") or 0)
        step_name = WORKFLOW_STEPS[current_step - 1] if 1 <= current_step <= len(WORKFLOW_STEPS) else "unknown"
        traceback_text = traceback.format_exc()
        self.workflow_state["failure"] = {
            "failed_step": current_step,
            "failed_step_name": step_name,
            "error_message": str(exc),
            "traceback": traceback_text,
            "partial_outputs_present": sorted(self.workflow_state.get("agent_outputs", {}).keys()),
            "recommended_retry_point": step_name,
        }

    def execute_workflow(self) -> dict[str, Any]:
        outputs = self.workflow_state["agent_outputs"]
        self.workflow_state["status"] = "running"
        self.workflow_state["failure"] = {}
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

            self._set_step(8, "running")
            self.workflow_state["generated_reports"]["pdf"] = generate_pdf_report(self.workflow_state)
            self._set_step(8, "done")

            self._set_step(9, "planning")
            outputs["presentation_architect"] = self.agents["presentation_architect"].execute(self.workflow_state)
            self._set_step(9, "running")
            self.workflow_state["generated_reports"]["slide_deck"] = generate_slide_deck(self.workflow_state)
            self._set_step(9, "done")
            self._update_run_manifest(final_code=final_code)

            self.workflow_state["status"] = "completed"
        except Exception as exc:
            self._logger.error("Workflow failed: %s", exc)
            self._logger.error(traceback.format_exc())
            self._record_failure(exc)
            self._update_run_manifest()
            self.workflow_state["status"] = "error"
        return self.workflow_state

    def _update_run_manifest(self, final_code: str = "") -> None:
        self.workflow_state["run_manifest"].update(
            {
                "figures": list(self.workflow_state.get("saved_figures", [])),
                "reports": dict(self.workflow_state.get("generated_reports", {})),
                "warnings": list(self.workflow_state.get("analysis_artifact_warnings", [])),
                "agent_outputs": sorted(self.workflow_state.get("agent_outputs", {}).keys()),
                "final_code_present": bool(final_code or self.workflow_state.get("agent_outputs", {}).get("final_code")),
            }
        )

    def _coding_loop(self, analysis_plan: dict[str, Any], max_iterations: int = 4) -> str:
        current_code = ""
        approved_code = ""
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
                        "Reviewer returned malformed review (decision=%r, parse_error=%r). Treating as revision required.",
                        review.get("decision"),
                        review.get("parse_error"),
                    )
                    decision = "REVISE"
                objective_gaps = self._objective_coverage_gaps(execution)
                self.agents["coder"].context["review_feedback"] = json_dumps_safe(
                    {
                        "execution": execution,
                        "artifact_issues": artifact_issues,
                        "objective_coverage_gaps": objective_gaps,
                        "review": review,
                    },
                    indent=2,
                )
                last_execution_error = " ; ".join(artifact_issues + objective_gaps) or review.get("summary", "Reviewer requested revision.")
                if decision == "APPROVE":
                    approved_code = current_code
                    self.workflow_state["agent_outputs"]["final_code_review"] = review
                    self._set_step(5, "done")
                    break
                if iteration < max_iterations - 1:
                    self._set_step(5, "revise")
                    continue
                self._set_step(5, "failed")
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
                                f"Use only installed/allowed analytics packages. Replace '{missing_module}' "
                                "with pandas, numpy, matplotlib, seaborn, scipy, statsmodels, or sklearn when possible."
                            ),
                            (
                                f"If the package was only forgotten in the import section, add the explicit "
                                f"`import {missing_module}` only if it is already installed and allowed."
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
        if approved_code:
            self._set_step(5, "done")
            return approved_code
        self._set_step(5, "failed")
        raise RuntimeError(
            f"No reviewer-approved analysis code was generated after {max_iterations} attempts. "
            f"Last execution error: {last_execution_error}"
        )

    def _objective_coverage_gaps(self, execution: dict[str, Any]) -> list[str]:
        objective = self.workflow_state.get("workflow_objective", {}) or {}
        if not objective.get("raw_description"):
            return []
        summary = execution.get("analysis_summary", {}) or {}
        findings = execution.get("business_findings", []) or []
        combined = f"{json_dumps_safe(summary)} {json_dumps_safe(findings)}".lower()
        gaps: list[str] = []
        if "user_goal_alignment" not in summary:
            gaps.append("analysis_summary must include user_goal_alignment for the stated objective.")
        focus_terms = [str(term).lower() for term in objective.get("focus_terms", [])[:5]]
        missing_terms = [term for term in focus_terms if term not in combined]
        if focus_terms and len(missing_terms) == len(focus_terms):
            gaps.append("Analysis findings do not clearly reference the user's stated focus terms.")
        return gaps

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
            safety_issues = self._analysis_code_safety_issues(code)
            if safety_issues:
                return {
                    "execution_status": "failed",
                    "error": "Unsafe analysis code blocked: " + "; ".join(safety_issues),
                    "traceback": "",
                    "safety_issues": safety_issues,
                }
            install_result = self._install_missing_allowed_imports(code)
            if install_result:
                return install_result
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
                "__builtins__": SAFE_BUILTINS,
                "__name__": "__analysis__",
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
            if not isinstance(analysis_summary, dict) or not analysis_summary:
                analysis_summary = self._fallback_analysis_summary(figures, loaded_dataframes)
            business_findings = self._normalize_business_findings(
                make_json_safe(exec_globals.get("business_findings", [])),
                analysis_summary,
                figure_captions,
            )
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

    def _fallback_analysis_summary(self, figures: list[str], loaded_dataframes: list[pd.DataFrame]) -> dict[str, Any]:
        total_rows = int(sum(len(df) for df in loaded_dataframes))
        total_columns = int(sum(len(df.columns) for df in loaded_dataframes))
        return {
            "execution_note": "Analysis code executed but did not define analysis_summary; runtime generated a fallback summary.",
            "datasets_analyzed": len(loaded_dataframes),
            "total_rows": total_rows,
            "total_columns": total_columns,
            "figures_generated": len(figures),
        }

    def _analysis_code_safety_issues(self, code: str) -> list[str]:
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return [f"code does not parse as Python: {exc.msg}"]
        issues: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._validate_import_root(alias.name, issues)
            elif isinstance(node, ast.ImportFrom):
                self._validate_import_root(node.module or "", issues)
            elif isinstance(node, ast.Call):
                call_name = self._call_name(node.func)
                if call_name in {"eval", "exec", "open", "__import__", "compile"}:
                    issues.append(f"blocked dangerous builtin call: {call_name}")
                if call_name.endswith(".system") or call_name.endswith(".popen"):
                    issues.append(f"blocked shell execution call: {call_name}")
                if "pip" in call_name.lower() or "install" in call_name.lower() and "subprocess" in call_name.lower():
                    issues.append(f"blocked package installation call: {call_name}")
        return sorted(set(issues))

    def _install_missing_allowed_imports(self, code: str) -> dict[str, Any] | None:
        roots = self._static_import_roots(code)
        install_attempts: list[dict[str, str]] = []
        for root in sorted(roots):
            package = APPROVED_ANALYSIS_PACKAGE_INSTALLS.get(root)
            if not package:
                continue
            if importlib.util.find_spec(root) is not None:
                continue
            self._logger.info("Installing approved analysis package: %s", package)
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", package],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                install_attempts.append({"module": root, "package": package, "status": "installed"})
            except Exception as exc:
                return {
                    "execution_status": "failed",
                    "error": f"Approved package install failed for '{package}': {exc}",
                    "traceback": traceback.format_exc(),
                    "missing_module": root,
                    "package_install_attempts": install_attempts
                    + [{"module": root, "package": package, "status": "failed"}],
                }
        return None

    def _static_import_roots(self, code: str) -> set[str]:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return set()
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root:
                        roots.add(root)
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".", 1)[0]
                if root:
                    roots.add(root)
        return roots

    def _validate_import_root(self, module_name: str, issues: list[str]) -> None:
        root = module_name.split(".", 1)[0]
        if not root:
            return
        if root in BLOCKED_ANALYSIS_MODULES:
            issues.append(f"blocked import: {root}")
            return
        if root not in ALLOWED_ANALYSIS_IMPORT_ROOTS:
            issues.append(f"import is not in the analysis allowlist: {root}")

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = self._call_name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        return ""

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
