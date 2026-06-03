from __future__ import annotations

import ast
import builtins
import difflib
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

import matplotlib

matplotlib.use("Agg")
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
    SharedContextStore,
)
from .dataset_paths import find_csv_files, prompt_dataset_paths, resolve_dataset_paths
from .decision_tree_figure import (
    build_sklearn_tree_artifact,
    decision_tree_performance_note,
    decision_tree_underperforms_baseline,
    render_decision_tree_rules_figure,
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
from .workflow_steps import WORKFLOW_STEPS, format_step_update

warnings.filterwarnings("ignore")
plt.style.use("seaborn-v0_8")

SLIDE_FIGURE_PALETTE = [
    "#2C65A4",
    "#35807F",
    "#BB9241",
    "#49845C",
    "#A8534C",
]
sns.set_palette(SLIDE_FIGURE_PALETTE)

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
        "divmod",
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
        "ord",
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
            "decision_tree_target_column": "",
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
                "analysis_loop_iterations": 0,
                "analysis_retry_errors": [],
                "analysis_retry_context_log": [],
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

    def set_decision_tree_target_column(self, target_column: str) -> None:
        normalized = target_column.strip()
        self.workflow_state["decision_tree_target_column"] = normalized
        for agent in self.agents.values():
            agent.set_shared_context(decision_tree_target_column=normalized)

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

    def available_columns(self) -> list[str]:
        columns: list[str] = []
        for df in self.workflow_state.get("csv_data", {}).values():
            for column in df.columns:
                name = str(column)
                if name not in columns:
                    columns.append(name)
        return columns

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

            self.agents["coder"].context["data_understanding"] = data_insights
            self.agents["reviewer"].context["data_understanding"] = data_insights
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
        self.workflow_state["run_manifest"]["analysis_retry_context_log"] = []
        self.agents["coder"].context.pop("code_loop_context_log", None)
        for iteration in range(max_iterations):
            self._set_step(4, f"running (iteration {iteration + 1})")
            self.agents["coder"].context["progress_callback"] = lambda status: self._set_step(4, status)
            self.agents["coder"].context["code_loop_context_log"] = self._format_code_loop_context_log()
            try:
                current_code = self.agents["coder"].execute(analysis_plan, self.workflow_state["csv_data"], iteration + 1)
            except Exception as exc:
                last_execution_error = str(exc)
                self._append_code_loop_context(
                    iteration=iteration + 1,
                    stage="generation",
                    status="failed",
                    error=last_execution_error,
                    guidance=[
                        "Return executable Python only and keep the required output contract complete before optional sections."
                    ],
                )
                self.agents["coder"].context["review_feedback"] = (
                    "The previous response was not a valid analysis script. "
                    "Return executable Python only with analysis_summary, business_findings, figure_captions, analysis_artifacts, and optional chart_specs.\n"
                    f"Error: {exc}\n"
                    f"Temporary code loop context log:\n{self._format_code_loop_context_log()}"
                )
                self._set_step(4, "revise")
                continue
            finally:
                self.agents["coder"].context.pop("progress_callback", None)
            self._set_step(4, "done")

            self._set_step(5, f"running (iteration {iteration + 1})")
            execution = self._execute_code(current_code)
            artifact_issues = self._analysis_output_issues(execution)
            if execution.get("execution_status") != "success":
                last_execution_error = execution.get("error", "Unknown execution error.")
                known_error_guidance = self._known_execution_error_guidance(last_execution_error)
                self.workflow_state["run_manifest"].setdefault("analysis_retry_errors", []).append(
                    str(last_execution_error)
                )
                self._append_code_loop_context(
                    iteration=iteration + 1,
                    stage="execution",
                    status="failed",
                    error=last_execution_error,
                    guidance=known_error_guidance,
                    execution=execution,
                    artifact_issues=artifact_issues,
                )
                self._logger.info(
                    "Analysis iteration %s execution failed: %s",
                    iteration + 1,
                    last_execution_error,
                )
            if execution.get("execution_status") == "success":
                self.workflow_state["analysis_results"] = execution
                self.workflow_state["saved_figures"] = execution.get("figures_generated", [])
                self.workflow_state["analysis_artifact_warnings"] = artifact_issues

                if not artifact_issues:
                    approved_code = current_code
                    self.workflow_state["run_manifest"]["analysis_loop_iterations"] = iteration + 1
                    self._set_step(5, "done")
                    break

                self._logger.info(
                    "Analysis iteration %s needs artifact revision: %s",
                    iteration + 1,
                    " | ".join(artifact_issues),
                )
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
                known_error_guidance = self._known_execution_error_guidance(last_execution_error)
                self._append_code_loop_context(
                    iteration=iteration + 1,
                    stage="artifact_review",
                    status="needs_revision",
                    error=last_execution_error,
                    guidance=known_error_guidance,
                    execution=execution,
                    artifact_issues=artifact_issues,
                    objective_gaps=objective_gaps,
                    review=review,
                )
                self.agents["coder"].context["review_feedback"] = json_dumps_safe(
                    {
                        "execution": execution,
                        "artifact_issues": artifact_issues,
                        "objective_coverage_gaps": objective_gaps,
                        "known_error_guidance": known_error_guidance,
                        "temporary_code_loop_context_log": self._current_code_loop_context_log(),
                        "review": review,
                    },
                    indent=2,
                )
                last_execution_error = " ; ".join(artifact_issues + objective_gaps) or review.get("summary", "Reviewer requested revision.")
                if decision == "APPROVE":
                    approved_code = current_code
                    self.workflow_state["run_manifest"]["analysis_loop_iterations"] = iteration + 1
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
                        "known_error_guidance": known_error_guidance,
                        "temporary_code_loop_context_log": self._current_code_loop_context_log(),
                        "review": {
                            "decision": "REVISE",
                            "summary": "The generated output was not runnable Python. Fix the runtime error and return executable code only.",
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
                            "known_error_guidance": known_error_guidance,
                            "temporary_code_loop_context_log": self._current_code_loop_context_log(),
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

    def _append_code_loop_context(
        self,
        *,
        iteration: int,
        stage: str,
        status: str,
        error: Any = "",
        guidance: list[str] | None = None,
        execution: dict[str, Any] | None = None,
        artifact_issues: list[str] | None = None,
        objective_gaps: list[str] | None = None,
        review: dict[str, Any] | None = None,
    ) -> None:
        log = self.workflow_state["run_manifest"].setdefault("analysis_retry_context_log", [])
        entry: dict[str, Any] = {
            "iteration": int(iteration),
            "stage": stage,
            "status": status,
            "error": self._compact_context_text(error),
            "guidance": [self._compact_context_text(item, limit=260) for item in (guidance or [])[:4]],
        }
        if artifact_issues:
            entry["artifact_issues"] = [self._compact_context_text(item, limit=240) for item in artifact_issues[:5]]
        if objective_gaps:
            entry["objective_gaps"] = [self._compact_context_text(item, limit=240) for item in objective_gaps[:5]]
        if execution:
            entry["execution_status"] = self._compact_context_text(execution.get("execution_status", ""))
            entry["figures_generated"] = len(execution.get("figures_generated", []) or [])
            entry["artifact_count"] = len(execution.get("analysis_artifacts", []) or [])
            traceback_text = execution.get("traceback")
            if traceback_text:
                entry["traceback_tail"] = self._compact_context_text(traceback_text, limit=500)
        if review:
            entry["review_decision"] = self._compact_context_text(review.get("decision", ""))
            entry["review_summary"] = self._compact_context_text(review.get("summary", ""), limit=320)
            critical = review.get("critical_issues") or []
            if isinstance(critical, list):
                entry["review_critical_issues"] = [
                    self._compact_context_text(item, limit=220) for item in critical[:3]
                ]
        log.append(entry)
        del log[:-5]
        self.agents["coder"].context["code_loop_context_log"] = self._format_code_loop_context_log()

    def _current_code_loop_context_log(self) -> list[dict[str, Any]]:
        return list(self.workflow_state["run_manifest"].get("analysis_retry_context_log", []) or [])

    def _format_code_loop_context_log(self) -> str:
        entries = self._current_code_loop_context_log()
        if not entries:
            return "No previous code-loop failures in this run."
        return json_dumps_safe(
            {
                "purpose": (
                    "Temporary bounded retry memory for this run only. Use it to avoid repeating the same "
                    "mistake in the next generated script."
                ),
                "recent_failures": entries[-5:],
            },
            indent=2,
        )

    def _compact_context_text(self, value: Any, limit: int = 360) -> str:
        text = _stringify(value).replace("\r", " ").replace("\n", " ")
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)].rstrip() + "..."

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

    def _known_execution_error_guidance(self, error: Any) -> list[str]:
        message = _stringify(error).lower()
        guidance: list[str] = []
        if "sklearn_utils" in message:
            guidance.append(
                "Remove the fake sklearn_utils import. build_sklearn_tree_artifact and render_decision_tree_rules_figure are already available in the analysis runtime; call them directly without importing them."
            )
        if "bin labels must be one fewer than the number of bin edges" in message:
            guidance.append(
                "Fix pandas binning: for pd.cut, labels length must equal len(bin_edges) - 1. "
                "When bin edges are dynamic, build labels after the final edges list or omit labels and rename categories later."
            )
            guidance.append(
                "For pd.qcut with duplicates='drop', do not pass a fixed labels list unless you first compute the dropped bin count; "
                "otherwise use labels=False or create labels from the returned categories."
            )
        if "bins must increase monotonically" in message:
            guidance.append(
                "Fix pandas binning: sort bin edges, remove duplicate edges, and skip binning when fewer than two unique numeric values remain."
            )
        if "name 'bins' is not defined" in message or "name \"bins\" is not defined" in message:
            guidance.append(
                "Fix bin variables: define bin_edges or bins before calling pd.cut, keep the same variable name throughout, "
                "and skip the binned chart when there are not enough unique numeric values."
            )
        if "keyerror" in message or re.fullmatch(r"'[^']+'", message):
            guidance.append(
                "Fix missing label lookups: use only columns and long-form row labels shown in the data profile. "
                "For metric/value summary tables, preserve observed metric labels, check a label is present, and use guarded lookups instead of invented normalized keys."
            )
            guidance.append(
                "Fix category lookups: do not invent category values such as loan intents, departments, or segments. "
                "Build rankings from observed grouped rows and use .reindex(..., fill_value=0) only with categories actually present in the data profile."
            )
            guidance.append(
                "Fix column lookups: use exact column names from df.columns and the data profile. "
                "If a likely column such as a satisfaction score is absent, choose an observed substitute and note the limitation."
            )
        if "charmap" in message and "codec can't encode" in message:
            guidance.append(
                "Fix console output: avoid Unicode symbols in print/debug text, or remove print calls entirely. "
                "Use ASCII-safe text in generated analysis scripts."
            )
        if "fillna() got an unexpected keyword argument 'method'" in message:
            guidance.append(
                "Fix missing-value handling: replace fillna(method='ffill') and fillna(method='bfill') with .ffill() and .bfill()."
            )
        if "invalid frequency" in message or "no longer supported for offsets" in message:
            guidance.append(
                "Fix pandas time frequencies: avoid bare offset aliases such as 'H', 'M', and 'Y' in resample, pd.Grouper, "
                "date_range, or rolling windows. Use pandas-safe aliases like 'h' for hourly, 'D' for daily, 'W' for weekly, "
                "'ME' for month-end, 'QE' for quarter-end, and 'YE' for year-end."
            )
            guidance.append(
                "For month or year display labels, prefer parsed datetime columns with .dt.to_period('M').astype(str) or "
                ".dt.to_period('Y').astype(str), then group by the label column instead of resampling with deprecated offsets."
            )
        if "knnimputer" in message and "object has no attribute" in message:
            guidance.append(
                "Fix imputation: do not call private sklearn internals such as KNNImputer._find_nearest_neighbors. "
                "Use public fit_transform on selected numeric columns or a simple median fill."
            )
        if "name 'ord' is not defined" in message or "name \"ord\" is not defined" in message:
            guidance.append(
                "Fix character handling: avoid ordinal encoding helpers unless needed; for categories, use pandas factorize or get_dummies instead of hand-written ord loops."
            )
        if "unsafe analysis code blocked" in message and (
            "import is not in the analysis allowlist" in message or "blocked import" in message
        ):
            guidance.append(
                "Remove filesystem and unapproved imports from generated analysis. Save figures with direct figure_#.png names and use approved analytics packages only."
            )
        if "invalid value" in message and "dtype" in message:
            guidance.append(
                "Keep numeric columns numeric. Store display labels such as month names in separate variables or label columns instead of assigning strings into numeric metric values."
            )
        if "all arrays must be of the same length" in message:
            guidance.append(
                "Fix EDA chart payloads: derive category labels, plotted values, annotations, and colors from the same aggregated rows so every plotted sequence has matching length."
            )
        if "must be a valid color" in message:
            guidance.append(
                "Fix EDA colors: pass explicit valid matplotlib color strings or one valid color per plotted group; do not build partial color arrays from mismatched subsets."
            )
        if "palette dictionary is missing keys" in message:
            guidance.append(
                "Fix seaborn palette mapping: avoid partial palette dictionaries. Either pass a palette list, or build the palette dict from the exact observed hue values after converting the hue column to strings."
            )
            guidance.append(
                "When plotting binary or categorical targets, normalize the hue column with .astype(str), compute sorted observed hue values, and create a color mapping for every observed value before calling seaborn."
            )
        if "dtype category cannot perform" in message or "category dtype does not support aggregation" in message:
            guidance.append(
                "Fix categorical math: convert category labels to strings before concatenation and cast metrics to numeric before arithmetic or mean/rate aggregation."
            )
            guidance.append(
                "When grouping mixed dataframes, aggregate only selected numeric metric columns or use named aggregations; never call mean() on the whole grouped dataframe."
            )
        if "dtype 'str' does not support operation 'mean'" in message or "does not support operation 'mean'" in message:
            guidance.append(
                "Fix aggregation: select only numeric metric columns for mean/sum/median calculations. "
                "Keep month names, season labels, and other display strings out of numeric aggregations."
            )
        if "could not convert string to float" in message:
            guidance.append(
                "Fix compact numeric parsing: convert strings such as '331.82K', '1.2M', percentages, and comma-formatted numbers with a helper before casting to float."
            )
        if "object is not iterable" in message:
            guidance.append(
                "Fix scalar/list handling: when building chart series or artifacts, wrap scalar numpy values in a one-row list and iterate over grouped rows, not aggregate scalars."
            )
        if "fragile metric lookup" in message:
            guidance.append(
                "Fix metric lookups: do not read derived metrics with row['mean'], stats['rate'], or summary['count'] unless that key was just created. "
                "Use named aggregations such as groupby(...).agg(mean_value=('col', 'mean')), convert to records, or use .get('mean', fallback) for describe()/dict summaries."
            )
        if "object has no attribute 'pearsonr'" in message:
            guidance.append(
                "Fix correlation code: do not shadow scipy.stats or pandas objects with dictionaries named stats. "
                "Use pandas Series.corr for simple pairwise correlations or import scipy.stats under an unshadowed name."
            )
        if "decision tree preflight" in message or "sklearn tree internals" in message or "feature_names_in_" in message:
            guidance.append(
                "Fix decision tree artifact creation: do not inspect .tree_, .feature, .threshold, .value, .values, "
                "or .feature_names_in_ in generated analysis code, and do not pass DecisionTreeClassifier/DecisionTreeRegressor classes. Train a fitted DecisionTree model or Pipeline, "
                "then call build_sklearn_tree_artifact(fitted_tree_pipeline_or_model, feature_names=None, target=..., "
                "model_type=..., train_score=..., test_score=..., baseline_score=...)."
            )
            guidance.append(self._decision_tree_allowed_pattern_guidance())
        if (
            "decisiontreeclassifier" in message
            or "decisiontreeregressor" in message
            or "property' object has no attribute" in message
            or "property\" object has no attribute" in message
        ) and ("feature" in message or "value" in message or "tree" in message):
            guidance.append(
                "Fix decision tree rules: do not read DecisionTreeClassifier.tree_, DecisionTreeRegressor.tree_, "
                ".feature, .value, or .values from sklearn classes/properties, and do not pass the DecisionTreeClassifier/DecisionTreeRegressor class itself. Train a model instance, then call "
                "build_sklearn_tree_artifact(fitted_tree_pipeline_or_model, feature_names=None, target=..., model_type=..., "
                "train_score=..., test_score=..., baseline_score=...)."
            )
            guidance.append(
                "If using a sklearn Pipeline, pass the fitted Pipeline directly to build_sklearn_tree_artifact; "
                "the helper can infer the final tree estimator and transformed feature names."
            )
        if "did not produce the required outputs" in message:
            guidance.append(
                "Fix output contract: assign analysis_summary, business_findings, figure_captions, and analysis_artifacts before optional modeling or extra visuals."
            )
        if "is not defined" in message:
            guidance.append(
                "Fix undefined variables: create every summary dataframe before referencing it, and build analysis_summary, "
                "business_findings, figure_captions, and analysis_artifacts only from variables already assigned in the script."
            )
        if "undefined generated name" in message:
            guidance.append(
                "Fix undefined generated names exactly as reported: use the closest assigned variable when it is a typo, "
                "or define the missing dataframe/summary before referencing it. Do not invent new columns or variables late in the script."
            )
        return guidance

    def _decision_tree_allowed_pattern_guidance(self) -> str:
        target = _stringify(self.workflow_state.get("decision_tree_target_column") or "target")
        return (
            "Mandatory decision-tree pattern: after train_test_split, fit a DecisionTreeClassifier/Regressor or Pipeline instance, "
            "compute train_score = fitted_model.score(X_train, y_train), test_score = fitted_model.score(X_test, y_test), "
            "compute a simple baseline score, then call "
            f"decision_tree_artifact = build_sklearn_tree_artifact(fitted_model_or_pipeline, feature_names=None, target='{target}', "
            "model_type='classification', train_score=train_score, test_score=test_score, baseline_score=baseline_score). "
            "Then call render_decision_tree_rules_figure(decision_tree_artifact, 'decision_tree_rules.png'), "
            "append decision_tree_artifact to analysis_artifacts, and set figure_captions['decision_tree_rules.png']. "
            "Do not create children_left, thresholds, values, node lists, or rules yourself."
        )

    def _analysis_output_issues(self, execution: dict[str, Any]) -> list[str]:
        issues: list[str] = []
        if execution.get("execution_status") != "success":
            issues.append("Analysis code did not execute successfully.")
            return issues

        figures = execution.get("figures_generated", []) or []
        structured_artifacts = execution.get("analysis_artifacts", []) or execution.get("chart_specs", []) or []
        analysis_summary = execution.get("analysis_summary", {}) or {}
        figure_captions = execution.get("figure_captions", {}) or {}
        business_findings = execution.get("business_findings", []) or []

        if len(structured_artifacts) < 4:
            issues.append(
                "Analysis produced fewer than 4 structured chart artifacts, so slides 4-7 cannot all rebuild visual analysis."
            )
        decision_tree_artifacts = [
            artifact
            for artifact in structured_artifacts
            if isinstance(artifact, dict) and str(artifact.get("chart_type", "")).strip().lower() == "decision_tree"
        ]
        if self.workflow_state.get("decision_tree_target_column") and not decision_tree_artifacts:
            issues.append(
                "A decision tree target column was provided, but analysis did not return a decision_tree rules artifact."
            )
        if self.workflow_state.get("decision_tree_target_column") and decision_tree_artifacts:
            if not any(self._decision_tree_artifact_has_figure(artifact, figures) for artifact in decision_tree_artifacts):
                issues.append(
                    "Decision tree artifact did not include a saved PNG figure such as decision_tree_rules.png for PDF and slides."
                )
            if not any(self._decision_tree_artifact_has_leaf_rules(artifact) for artifact in decision_tree_artifacts):
                issues.append("Decision tree artifact did not include readable leaf prediction/rule nodes.")
            if not any(self._decision_tree_artifact_has_valid_graph(artifact) for artifact in decision_tree_artifacts):
                issues.append(
                    "Decision tree artifact did not include a real split/leaf graph with connected true/false branches."
                )
            if not any(self._decision_tree_artifact_is_model_verified(artifact) for artifact in decision_tree_artifacts):
                issues.append(
                    "Decision tree rules were not marked as verified from the fitted model; slides may not match the model."
                )
            if not any(self._decision_tree_artifact_has_train_test_metrics(artifact) for artifact in decision_tree_artifacts):
                issues.append(
                    "Decision tree artifact did not include both training and test model metrics for report and slides."
                )
            if any(self._decision_tree_artifact_underperforms_without_warning(artifact) for artifact in decision_tree_artifacts):
                issues.append(
                    "Decision tree test accuracy is below baseline, but the artifact does not warn that it is explanatory only."
                )
        if figures and not structured_artifacts:
            issues.append("Analysis produced PNG figures but no structured chart artifacts; slide visuals cannot rebuild charts from images.")
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

    def _decision_tree_artifact_has_figure(self, artifact: dict[str, Any], figures: list[Any]) -> bool:
        data = artifact.get("data", {}) if isinstance(artifact.get("data"), dict) else {}
        candidates = [
            artifact.get("image_path"),
            artifact.get("fallback_path"),
            artifact.get("visual_path"),
            data.get("image_path"),
            data.get("fallback_path"),
            data.get("figure_path"),
        ]
        figure_set = {str(path) for path in figures if path}
        for candidate in candidates:
            if not candidate:
                continue
            path = str(candidate)
            if path in figure_set or os.path.exists(path):
                return True
        return False

    def _decision_tree_artifact_has_leaf_rules(self, artifact: dict[str, Any]) -> bool:
        data = artifact.get("data", {}) if isinstance(artifact.get("data"), dict) else {}
        nodes = [node for node in data.get("nodes", []) if isinstance(node, dict)]
        edges = [edge for edge in data.get("edges", []) if isinstance(edge, dict)]
        if nodes:
            sources = {self._tree_edge_endpoint(edge, ("source", "from", "parent")) for edge in edges}
            leaf_nodes = [
                node
                for node in nodes
                if self._tree_node_id(node) not in sources
            ] or nodes
            return any(
                re.search(r"\b(leaf|predict|prediction|class|value|rule|then)\b", _stringify(node).lower())
                for node in leaf_nodes
            )
        rules = data.get("rules", []) or artifact.get("rules", [])
        return any(_stringify(rule).strip() for rule in rules)

    def _decision_tree_artifact_has_valid_graph(self, artifact: dict[str, Any]) -> bool:
        data = artifact.get("data", {}) if isinstance(artifact.get("data"), dict) else {}
        nodes = [node for node in data.get("nodes", []) if isinstance(node, dict)]
        edges = [edge for edge in data.get("edges", []) if isinstance(edge, dict)]
        if not nodes or not edges:
            return False
        node_ids = {self._tree_node_id(node) for node in nodes if self._tree_node_id(node)}
        if not node_ids:
            return False
        source_ids = {
            self._tree_edge_endpoint(edge, ("source", "from", "parent"))
            for edge in edges
        }
        target_ids = {
            self._tree_edge_endpoint(edge, ("target", "to", "child"))
            for edge in edges
        }
        if not source_ids or not target_ids or not target_ids.issubset(node_ids):
            return False
        has_split = any(
            _stringify(node.get("type")).lower() == "split" or self._tree_node_id(node) in source_ids
            for node in nodes
        )
        leaf_nodes = [node for node in nodes if self._tree_node_id(node) not in source_ids]
        has_true_branch = any(_stringify(edge).lower().find("true") >= 0 for edge in edges)
        has_false_branch = any(_stringify(edge).lower().find("false") >= 0 for edge in edges)
        return has_split and len(leaf_nodes) >= 2 and has_true_branch and has_false_branch

    def _decision_tree_artifact_underperforms_without_warning(self, artifact: dict[str, Any]) -> bool:
        data = artifact.get("data", {}) if isinstance(artifact.get("data"), dict) else {}
        if not decision_tree_underperforms_baseline(data):
            return False
        text = " ".join(
            _stringify(value)
            for value in (
                artifact.get("finding"),
                artifact.get("takeaway"),
                data.get("performance_note"),
                data.get("model_note"),
                data.get("limitation"),
            )
        ).lower()
        return not ("explanatory" in text and ("baseline" in text or "production" in text))

    def _tree_node_id(self, node: dict[str, Any]) -> str:
        for key in ("id", "node_id", "name"):
            text = _stringify(node.get(key)).strip()
            if text:
                return text
        return ""

    def _tree_edge_endpoint(self, edge: dict[str, Any], keys: tuple[str, ...]) -> str:
        for key in keys:
            text = _stringify(edge.get(key)).strip()
            if text:
                return text
        return ""

    def _decision_tree_artifact_is_model_verified(self, artifact: dict[str, Any]) -> bool:
        data = artifact.get("data", {}) if isinstance(artifact.get("data"), dict) else {}
        return bool(
            data.get("model_verified")
            or data.get("rules_match_model")
            or _stringify(data.get("rules_source", "")).startswith("sklearn_tree")
        )

    def _decision_tree_artifact_has_train_test_metrics(self, artifact: dict[str, Any]) -> bool:
        data = artifact.get("data", {}) if isinstance(artifact.get("data"), dict) else {}
        classification_metrics = any(
            data.get(key) not in (None, "")
            for key in ("train_accuracy", "training_accuracy", "train_score")
        ) and any(
            data.get(key) not in (None, "")
            for key in ("test_accuracy", "testing_accuracy", "test_score")
        )
        regression_metrics = any(
            data.get(key) not in (None, "")
            for key in ("train_r2", "training_r2", "train_score")
        ) and any(
            data.get(key) not in (None, "")
            for key in ("test_r2", "testing_r2", "test_score")
        )
        return classification_metrics or regression_metrics

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
        return findings

    def _execute_code(self, code: str) -> dict[str, Any]:
        original_pyplot_savefig = plt.savefig
        original_figure_savefig = Figure.savefig
        exec_globals: dict[str, Any] = {}
        run_stamp = ""
        saved_figure_paths: list[str] = []
        figure_name_map: dict[str, str] = {}
        try:
            safety_issues = self._analysis_code_safety_issues(code)
            if safety_issues:
                return {
                    "execution_status": "failed",
                    "error": "Unsafe analysis code blocked: " + "; ".join(safety_issues),
                    "traceback": "",
                    "safety_issues": safety_issues,
                }
            preflight_issues = self._analysis_code_preflight_issues(code)
            if preflight_issues:
                return {
                    "execution_status": "failed",
                    "error": "Analysis code preflight failed: " + "; ".join(preflight_issues),
                    "traceback": "",
                    "preflight_issues": preflight_issues,
                }
            install_result = self._install_missing_allowed_imports(code)
            if install_result:
                return install_result
            def safe_print(*args: Any, **kwargs: Any) -> None:
                return None

            safe_builtins = dict(SAFE_BUILTINS)
            safe_builtins["print"] = safe_print
            exec_globals = {
                "pd": pd,
                "np": np,
                "plt": plt,
                "sns": sns,
                "build_sklearn_tree_artifact": build_sklearn_tree_artifact,
                "render_decision_tree_rules_figure": render_decision_tree_rules_figure,
                "nan": np.nan,
                "NaN": np.nan,
                "inf": np.inf,
                "Infinity": np.inf,
                "null": None,
                "NULL": None,
                "true": True,
                "false": False,
                "__builtins__": safe_builtins,
                "__name__": "__analysis__",
            }
            run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            saved_figure_paths = []
            figure_name_map = {}

            def resolve_figure_path(filename: Any) -> Any:
                if not isinstance(filename, (str, Path)):
                    return filename
                raw_path = Path(filename)
                raw_name = str(raw_path)
                if raw_path.suffix.lower() != ".png":
                    return filename
                if not (re.fullmatch(r"figure_\d+", raw_path.stem) or raw_path.stem == "decision_tree_rules"):
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
            return self._collect_analysis_execution_outputs(
                exec_globals,
                saved_figure_paths,
                figure_name_map,
                run_stamp,
            )
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
            try:
                recovered = self._collect_analysis_execution_outputs(
                    exec_globals,
                    saved_figure_paths,
                    figure_name_map,
                    run_stamp,
                    execution_error=exc,
                )
            except Exception:
                recovered = {}
            if recovered.get("execution_status") == "success":
                return recovered
            return {"execution_status": "failed", "error": str(exc), "traceback": traceback.format_exc()}
        finally:
            plt.savefig = original_pyplot_savefig
            Figure.savefig = original_figure_savefig

    def _collect_analysis_execution_outputs(
        self,
        exec_globals: dict[str, Any],
        saved_figure_paths: list[str],
        figure_name_map: dict[str, str],
        run_stamp: str,
        execution_error: Exception | None = None,
    ) -> dict[str, Any]:
        figures = [path for path in saved_figure_paths if os.path.exists(path)]
        self.workflow_state["saved_figures"] = figures
        raw_analysis_summary = make_json_safe(exec_globals.get("analysis_summary"))
        raw_business_findings = make_json_safe(exec_globals.get("business_findings"))
        raw_figure_captions = make_json_safe(exec_globals.get("figure_captions"))
        raw_analysis_artifacts = make_json_safe(exec_globals.get("analysis_artifacts"))
        missing_contract_items = self._missing_analysis_contract_items(
            raw_analysis_summary,
            raw_business_findings,
            raw_figure_captions,
            raw_analysis_artifacts,
        )
        if missing_contract_items:
            if execution_error is not None:
                return {}
            return {
                "execution_status": "failed",
                "error": (
                    "Analysis script did not produce the required outputs: "
                    + ", ".join(missing_contract_items)
                ),
                "traceback": "",
                "figures_generated": figures,
            }
        figure_captions = {}
        if isinstance(raw_figure_captions, dict):
            for key, value in raw_figure_captions.items():
                mapped_key = figure_name_map.get(str(key), str(key))
                figure_captions[mapped_key] = make_json_safe(value)
        analysis_summary = raw_analysis_summary if isinstance(raw_analysis_summary, dict) else {}
        if execution_error is not None:
            analysis_summary = dict(analysis_summary)
            analysis_summary["execution_recovered_from_error"] = _stringify(execution_error)
        business_findings = self._normalize_business_findings(raw_business_findings)
        raw_chart_specs = make_json_safe(exec_globals.get("chart_specs", []))
        raw_analysis_artifacts = self._map_artifact_figure_paths(raw_analysis_artifacts, figure_name_map)
        raw_chart_specs = self._map_artifact_figure_paths(raw_chart_specs, figure_name_map)
        chart_specs = self._normalize_chart_specs(raw_chart_specs)
        analysis_artifacts = self._normalize_analysis_artifacts(
            raw_analysis_artifacts,
            chart_specs,
        )
        analysis_artifacts = self._enrich_decision_tree_artifacts_from_runtime(analysis_artifacts, exec_globals)
        self._ensure_decision_tree_figures(analysis_artifacts, figure_captions, saved_figure_paths, run_stamp)
        figures = [path for path in saved_figure_paths if os.path.exists(path)]
        result = {
            "execution_status": "success",
            "figures_generated": figures,
            "analysis_summary": analysis_summary,
            "business_findings": business_findings,
            "figure_captions": figure_captions,
            "chart_specs": chart_specs,
            "analysis_artifacts": analysis_artifacts,
        }
        if execution_error is not None:
            result["recovered_from_error"] = _stringify(execution_error)
        return result

    def _normalize_chart_specs(self, raw_chart_specs: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_chart_specs, list):
            return []
        normalized: list[dict[str, Any]] = []
        allowed_types = {
            "bar",
            "column",
            "grouped_bar",
            "line",
            "scatter",
            "horizontal_bar",
            "ranking",
            "small_multiples_bar",
            "distribution",
            "metric_cards",
            "comparison",
            "decision_tree",
        }
        for index, item in enumerate(raw_chart_specs, start=1):
            if not isinstance(item, dict):
                continue
            chart_type = str(item.get("chart_type", "")).strip().lower()
            if chart_type not in allowed_types:
                continue
            rows = item.get("data") or item.get("rows") or []
            series = item.get("series") or []
            tree_payload = rows if chart_type == "decision_tree" and isinstance(rows, dict) else {}
            if isinstance(rows, dict):
                rows = rows.get("rows", [])
            has_rows = isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
            has_series = isinstance(series, list) and all(isinstance(entry, dict) for entry in series)
            has_tree_payload = (
                chart_type == "decision_tree"
                and isinstance(tree_payload, dict)
                and (
                    isinstance(tree_payload.get("nodes"), list)
                    or isinstance(tree_payload.get("rules"), list)
                )
            )
            if not has_rows and not has_series and not has_tree_payload:
                continue
            if has_rows and len(rows) > 50:
                rows = rows[:50]
            if has_series and len(series) > 8:
                series = series[:8]
            normalized_data: Any = rows
            if has_tree_payload:
                normalized_data = {
                    **tree_payload,
                    "nodes": list(tree_payload.get("nodes", []))[:15],
                    "edges": list(tree_payload.get("edges", []))[:20],
                    "rules": list(tree_payload.get("rules", []))[:10],
                }
                normalized_data = self._normalize_decision_tree_metric_aliases(normalized_data)
            spec = {
                "id": str(item.get("id") or f"chart_{index}"),
                "artifact_id": str(item.get("artifact_id") or item.get("id") or f"chart_{index}"),
                "artifact_type": "chart_spec",
                "slide_candidate": bool(item.get("slide_candidate", True)),
                "chart_type": chart_type,
                "title": _stringify(item.get("title", "")),
                "finding": _stringify(item.get("finding", "")),
                "takeaway": _stringify(item.get("takeaway", "")),
                "x": _stringify(item.get("x", "")),
                "y": _stringify(item.get("y", "")),
                "group_by": _stringify(item.get("group_by", "")),
                "x_label": _stringify(item.get("x_label", "")),
                "y_label": _stringify(item.get("y_label", "")),
                "value_format": _stringify(item.get("value_format", "")),
                "render_mode": _stringify(item.get("render_mode", "")),
                "series": series if has_series else _stringify(item.get("series") or item.get("group_by") or ""),
                "data": normalized_data,
                "image_path": _stringify(item.get("image_path", "")),
                "visual_path": _stringify(item.get("visual_path", "")),
                "fallback_path": _stringify(item.get("fallback_path", "")),
                "recommended_template": _stringify(item.get("recommended_template", "")),
            }
            normalized.append(spec)
        return normalized

    def _normalize_decision_tree_metric_aliases(self, data: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(data)
        model_type = _stringify(normalized.get("model_type", "")).lower()
        classification_like = model_type.startswith("class") or not model_type.startswith("reg")
        if classification_like:
            if normalized.get("train_accuracy") in (None, "") and normalized.get("train_score") not in (None, ""):
                normalized["train_accuracy"] = self._format_accuracy_value(normalized.get("train_score"))
            if normalized.get("test_accuracy") in (None, "") and normalized.get("test_score") not in (None, ""):
                normalized["test_accuracy"] = self._format_accuracy_value(normalized.get("test_score"))
            if normalized.get("baseline_accuracy") in (None, "") and normalized.get("baseline_score") not in (None, ""):
                normalized["baseline_accuracy"] = self._format_accuracy_value(normalized.get("baseline_score"))
        else:
            if normalized.get("train_r2") in (None, "") and normalized.get("train_score") not in (None, ""):
                normalized["train_r2"] = self._format_score_value(normalized.get("train_score"))
            if normalized.get("test_r2") in (None, "") and normalized.get("test_score") not in (None, ""):
                normalized["test_r2"] = self._format_score_value(normalized.get("test_score"))
        return normalized

    def _format_accuracy_value(self, value: Any) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return _stringify(value)
        if 0 <= number <= 1:
            number *= 100
        return f"{number:.1f}%"

    def _format_score_value(self, value: Any) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return _stringify(value)
        return f"{number:.3g}"

    def _map_artifact_figure_paths(self, value: Any, figure_name_map: dict[str, str]) -> Any:
        if not figure_name_map:
            return value
        if isinstance(value, dict):
            return {key: self._map_artifact_figure_paths(item, figure_name_map) for key, item in value.items()}
        if isinstance(value, list):
            return [self._map_artifact_figure_paths(item, figure_name_map) for item in value]
        if isinstance(value, str):
            return figure_name_map.get(value, value)
        return value

    def _ensure_decision_tree_figures(
        self,
        analysis_artifacts: list[dict[str, Any]],
        figure_captions: dict[str, Any],
        saved_figure_paths: list[str],
        run_stamp: str,
    ) -> None:
        tree_index = 0
        for artifact in analysis_artifacts:
            if str(artifact.get("chart_type", "")).strip().lower() != "decision_tree":
                continue
            tree_index += 1
            existing_path = _stringify(
                artifact.get("image_path") or artifact.get("fallback_path") or artifact.get("visual_path")
            )
            if existing_path and os.path.exists(existing_path):
                if existing_path not in saved_figure_paths:
                    saved_figure_paths.append(existing_path)
                continue
            existing_tree_figure = self._existing_decision_tree_figure(saved_figure_paths)
            if existing_tree_figure:
                artifact["fallback_path"] = existing_tree_figure
                artifact["image_path"] = existing_tree_figure
                figure_captions.setdefault(
                    existing_tree_figure,
                    _stringify(
                        artifact.get("takeaway")
                        or artifact.get("finding")
                        or "Decision tree leaf rules summarize the model logic."
                    ),
                )
                continue
            output_path = (
                f"decision_tree_rules_{run_stamp}.png"
                if tree_index == 1
                else f"decision_tree_rules_{tree_index}_{run_stamp}.png"
            )
            rendered = render_decision_tree_rules_figure(artifact, output_path)
            if not rendered:
                continue
            artifact["fallback_path"] = rendered
            artifact["image_path"] = rendered
            if rendered not in saved_figure_paths:
                saved_figure_paths.append(rendered)
            figure_captions.setdefault(
                rendered,
                _stringify(
                    artifact.get("takeaway")
                    or artifact.get("finding")
                    or "Decision tree leaf rules summarize the model logic."
                ),
            )

    def _existing_decision_tree_figure(self, saved_figure_paths: list[str]) -> str:
        for path in saved_figure_paths:
            name = Path(str(path)).name.lower()
            if name.startswith("decision_tree_rules") and str(path) and os.path.exists(path):
                return str(path)
        return ""

    def _enrich_decision_tree_artifacts_from_runtime(
        self,
        analysis_artifacts: list[dict[str, Any]],
        runtime_values: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not self.workflow_state.get("decision_tree_target_column"):
            return analysis_artifacts
        tree_artifacts = [
            artifact
            for artifact in analysis_artifacts
            if str(artifact.get("chart_type", "")).strip().lower() == "decision_tree"
        ]
        needs_verified_artifact = not tree_artifacts or any(
            not self._decision_tree_artifact_has_leaf_rules(artifact)
            or not self._decision_tree_artifact_has_valid_graph(artifact)
            or not self._decision_tree_artifact_is_model_verified(artifact)
            or not self._decision_tree_artifact_has_train_test_metrics(artifact)
            or self._decision_tree_artifact_underperforms_without_warning(artifact)
            for artifact in tree_artifacts
        )
        if not needs_verified_artifact:
            return analysis_artifacts

        model_context = self._runtime_decision_tree_model_context(runtime_values)
        if not model_context:
            return analysis_artifacts
        model = model_context["model"]
        scorer = model_context.get("scorer") or model
        feature_names = model_context["feature_names"]
        model_type = model_context["model_type"]
        train_score = self._runtime_metric(runtime_values, ("train_accuracy", "training_accuracy", "train_score", "train_r2", "training_r2"))
        test_score = self._runtime_metric(runtime_values, ("test_accuracy", "testing_accuracy", "test_score", "test_r2", "testing_r2"))
        baseline_score = self._runtime_metric(runtime_values, ("baseline_accuracy", "baseline_score", "majority_baseline"))
        train_mae = self._runtime_metric(runtime_values, ("train_mae", "training_mae"))
        test_mae = self._runtime_metric(runtime_values, ("test_mae", "testing_mae"))

        if train_score in (None, "") or test_score in (None, ""):
            inferred_scores = self._score_runtime_model(scorer, runtime_values)
            train_score = train_score if train_score not in (None, "") else inferred_scores.get("train_score")
            test_score = test_score if test_score not in (None, "") else inferred_scores.get("test_score")

        if model_type == "classification" and baseline_score in (None, ""):
            baseline_score = self._runtime_classification_baseline(runtime_values)

        existing = tree_artifacts[0] if tree_artifacts else {}
        safe_finding = self._decision_tree_runtime_finding(
            str(self.workflow_state.get("decision_tree_target_column", "")),
            model_type,
            test_score,
            baseline_score,
        )
        try:
            verified_artifact = build_sklearn_tree_artifact(
                model,
                feature_names,
                target=str(self.workflow_state.get("decision_tree_target_column", "")),
                model_type=model_type,
                train_score=train_score,
                test_score=test_score,
                baseline_score=baseline_score,
                train_mae=train_mae,
                test_mae=test_mae,
                class_names=[str(item) for item in getattr(model, "classes_", [])],
                title=_stringify(existing.get("title") or ""),
                finding=safe_finding,
            )
        except Exception:
            return analysis_artifacts

        if existing:
            if existing.get("fallback_path"):
                verified_artifact["fallback_path"] = existing.get("fallback_path")
            if existing.get("image_path"):
                verified_artifact["image_path"] = existing.get("image_path")

        replaced = False
        enriched: list[dict[str, Any]] = []
        for artifact in analysis_artifacts:
            if str(artifact.get("chart_type", "")).strip().lower() == "decision_tree" and not replaced:
                enriched.append(verified_artifact)
                replaced = True
            elif str(artifact.get("chart_type", "")).strip().lower() == "decision_tree":
                continue
            else:
                enriched.append(artifact)
        if not replaced:
            enriched.append(verified_artifact)
        return enriched

    def _decision_tree_runtime_finding(
        self,
        target: str,
        model_type: str,
        test_score: Any,
        baseline_score: Any,
    ) -> str:
        if str(model_type).lower().startswith("reg"):
            return (
                f"Interpretable decision tree rules explain variation in {target or 'the target'}; "
                "use R2 and MAE as model diagnostics, not classification accuracy."
            )
        note = decision_tree_performance_note(
            {
                "model_type": "classification",
                "test_accuracy": test_score,
                "baseline_accuracy": baseline_score,
            }
        )
        if note:
            return note
        return (
            f"Verified decision tree rules explain {target or 'the target'} patterns; "
            "compare test accuracy with baseline before predictive use."
        )

    def _runtime_decision_tree_model_context(self, runtime_values: dict[str, Any]) -> dict[str, Any] | None:
        candidates: list[tuple[int, str, Any, Any]] = []
        for name, value in runtime_values.items():
            if name.startswith("__"):
                continue
            lowered = name.lower()
            priority = 0
            if any(term in lowered for term in ("decision", "tree", "clf", "regressor", "model", "pipeline")):
                priority += 10
            model = self._tree_estimator(value)
            if model is None:
                continue
            candidates.append((priority, name, value, model))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        _, _, scorer, model = candidates[0]
        feature_names = self._runtime_tree_feature_names(scorer, model, runtime_values)
        if not feature_names:
            return None
        model_type = "classification" if hasattr(model, "classes_") else "regression"
        return {"scorer": scorer, "model": model, "feature_names": feature_names, "model_type": model_type}

    def _tree_estimator(self, value: Any) -> Any | None:
        if self._has_fitted_tree_object(value):
            return value
        steps = getattr(value, "steps", None)
        if steps:
            for _, estimator in reversed(steps):
                if self._has_fitted_tree_object(estimator):
                    return estimator
        named_steps = getattr(value, "named_steps", None)
        if named_steps:
            for estimator in reversed(list(named_steps.values())):
                if self._has_fitted_tree_object(estimator):
                    return estimator
        return None

    def _has_fitted_tree_object(self, value: Any) -> bool:
        if isinstance(value, type):
            return False
        tree = getattr(value, "tree_", None)
        if isinstance(tree, property) or tree is None:
            return False
        return all(hasattr(tree, name) for name in ("children_left", "children_right", "feature", "threshold", "value"))

    def _runtime_tree_feature_names(self, scorer: Any, model: Any, runtime_values: dict[str, Any]) -> list[str]:
        try:
            if scorer is not model and hasattr(scorer, "__getitem__"):
                names = scorer[:-1].get_feature_names_out()
                return [str(name) for name in names]
        except Exception:
            pass
        names = getattr(model, "feature_names_in_", None)
        if names is not None:
            return [str(name) for name in names]
        for key in ("feature_names", "model_feature_names", "encoded_feature_names", "selected_features"):
            value = runtime_values.get(key)
            if isinstance(value, (list, tuple)) and value:
                return [str(item) for item in value]
        for key in ("X_train", "x_train", "features_train", "X"):
            value = runtime_values.get(key)
            columns = getattr(value, "columns", None)
            if columns is not None:
                return [str(column) for column in columns]
        try:
            feature_count = int(getattr(model, "n_features_in_", 0) or getattr(model.tree_, "n_features", 0))
        except Exception:
            feature_count = 0
        return [f"feature_{index}" for index in range(feature_count)]

    def _runtime_metric(self, runtime_values: dict[str, Any], names: tuple[str, ...]) -> Any:
        for name in names:
            if name in runtime_values and runtime_values[name] not in (None, ""):
                return runtime_values[name]
        lowered_names = {name.lower() for name in names}
        for name, value in runtime_values.items():
            if name.lower() in lowered_names and value not in (None, ""):
                return value
        return None

    def _score_runtime_model(self, scorer: Any, runtime_values: dict[str, Any]) -> dict[str, Any]:
        scores: dict[str, Any] = {}
        pairs = [
            ("train_score", ("X_train", "x_train", "features_train"), ("y_train", "target_train")),
            ("test_score", ("X_test", "x_test", "features_test"), ("y_test", "target_test")),
        ]
        for output_key, x_names, y_names in pairs:
            x_value = next((runtime_values.get(name) for name in x_names if name in runtime_values), None)
            y_value = next((runtime_values.get(name) for name in y_names if name in runtime_values), None)
            if x_value is None or y_value is None or not hasattr(scorer, "score"):
                continue
            try:
                scores[output_key] = scorer.score(x_value, y_value)
            except Exception:
                continue
        return scores

    def _runtime_classification_baseline(self, runtime_values: dict[str, Any]) -> Any:
        y_value = next((runtime_values.get(name) for name in ("y_train", "target_train", "y") if name in runtime_values), None)
        if y_value is None:
            return None
        try:
            series = pd.Series(y_value)
            counts = series.value_counts(dropna=True)
            if counts.empty:
                return None
            return float(counts.iloc[0] / counts.sum())
        except Exception:
            return None

    def _normalize_analysis_artifacts(
        self,
        raw_artifacts: Any,
        chart_specs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        if isinstance(raw_artifacts, list):
            artifacts.extend(item for item in raw_artifacts if isinstance(item, dict))
        artifacts.extend(chart_specs)
        normalized = self._normalize_chart_specs(artifacts)
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for artifact in normalized:
            artifact_id = str(artifact.get("artifact_id") or artifact.get("id"))
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            unique.append(artifact)
        return unique

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
                lowered_call = call_name.lower()
                if lowered_call in {"pip", "pip.main"} or (
                    "install" in lowered_call and "subprocess" in lowered_call
                ):
                    issues.append(f"blocked package installation call: {call_name}")
        return sorted(set(issues))

    def _analysis_code_preflight_issues(self, code: str) -> list[str]:
        issues: list[str] = []
        compact = re.sub(r"\s+", "", code).lower()
        if "fillna(method=" in compact:
            issues.append("replace fillna(method=...) with .ffill() or .bfill() before execution")
        if re.search(r"\.resample\(\s*['\"][HMY]['\"]", code) or re.search(
            r"pd\.Grouper\([^)]*freq\s*=\s*['\"][HMY]['\"]",
            code,
        ):
            issues.append("use explicit pandas offset aliases such as h, ME, or YE instead of deprecated H/M/Y")
        lowered = code.lower()
        if "._find_nearest_neighbors" in code or "knnimputer._" in lowered:
            issues.append("do not call private sklearn internals; use public fit_transform or simple imputation")
        issues.extend(self._pd_cut_preflight_issues(code))
        if re.search(r"\.groupby\([^)]*\)\.mean\(", code, flags=re.DOTALL):
            issues.append("avoid groupby(...).mean() on whole dataframes; use named aggregations on selected numeric metrics")
        if re.search(r"for\s+\w+\s+in\s+np\.(?:float|float64|mean|median|sum)", code):
            issues.append("do not iterate numpy scalar values; wrap scalar metrics in a one-row artifact data list")
        issues.extend(self._undefined_name_preflight_issues(code))
        issues.extend(self._fragile_metric_lookup_preflight_issues(code))
        if self._uses_forbidden_tree_introspection(code):
            issues.append(
                "decision tree preflight: do not inspect sklearn tree internals (.tree_, .feature, .threshold, .value, or .values); use build_sklearn_tree_artifact on the fitted model or Pipeline"
            )
        if self._passes_tree_class_to_artifact_helper(code):
            issues.append(
                "decision tree preflight: pass a fitted DecisionTree model or fitted Pipeline to build_sklearn_tree_artifact, not the DecisionTreeClassifier/DecisionTreeRegressor class"
            )
        unsupported_tree_keywords = self._unsupported_tree_helper_keywords(code)
        if unsupported_tree_keywords:
            issues.append(
                "decision tree preflight: pass the fitted model or Pipeline as the first positional argument to build_sklearn_tree_artifact; unsupported model keyword(s): "
                + ", ".join(unsupported_tree_keywords)
            )
        if re.search(r"\.feature_names_in_\b", code) and "build_sklearn_tree_artifact" in code:
            issues.append(
                "decision tree preflight: do not read feature_names_in_ in generated code; pass feature_names=None to build_sklearn_tree_artifact when using a fitted Pipeline/model"
            )
        return sorted(set(issues))

    def _pd_cut_preflight_issues(self, code: str) -> list[str]:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []
        issues: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or self._call_name(node.func) != "pd.cut":
                continue
            bins_node = None
            labels_node = None
            if len(node.args) >= 2:
                bins_node = node.args[1]
            for keyword in node.keywords:
                if keyword.arg == "bins":
                    bins_node = keyword.value
                elif keyword.arg == "labels":
                    labels_node = keyword.value
            if labels_node is None:
                continue
            label_count = self._literal_sequence_length(labels_node)
            if label_count is None:
                continue
            bin_count = self._literal_bin_count(bins_node)
            if bin_count is None:
                issues.append(
                    "avoid fixed pd.cut labels with dynamic bins; derive labels after final bin edges or omit labels"
                )
            elif label_count != bin_count:
                issues.append(
                    f"pd.cut labels must match bins: got {label_count} labels for {bin_count} output bins"
                )
        return issues

    def _literal_sequence_length(self, node: ast.AST | None) -> int | None:
        if isinstance(node, (ast.List, ast.Tuple)):
            return len(node.elts)
        return None

    def _literal_bin_count(self, node: ast.AST | None) -> int | None:
        if isinstance(node, (ast.List, ast.Tuple)):
            return max(len(node.elts) - 1, 0)
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return max(node.value, 0)
        return None

    def _undefined_name_preflight_issues(self, code: str) -> list[str]:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []
        assigned: set[str] = set()
        loaded: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                assigned.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    assigned.add(alias.asname or alias.name.split(".", 1)[0])
            elif isinstance(node, ast.arg):
                assigned.add(node.arg)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                assigned.add(str(node.name))
            elif isinstance(node, ast.Name):
                if isinstance(node.ctx, ast.Store):
                    assigned.add(node.id)
                elif isinstance(node.ctx, ast.Load):
                    loaded.add(node.id)
            for child_name in ("target", "targets", "optional_vars"):
                child = getattr(node, child_name, None)
                if isinstance(child, list):
                    for target in child:
                        self._collect_assigned_target_names(target, assigned)
                elif isinstance(child, ast.AST):
                    self._collect_assigned_target_names(child, assigned)
        allowed = self._preloaded_analysis_names() | assigned
        missing = sorted(name for name in loaded if name not in allowed and not name.startswith("__"))
        if not missing:
            return []
        issues = []
        for name in missing[:4]:
            suggestion = difflib.get_close_matches(name, sorted(assigned), n=1, cutoff=0.78)
            if suggestion:
                issues.append(f"undefined generated name: {name}; did you mean {suggestion[0]}?")
            else:
                issues.append(f"undefined generated name: {name}; define it before use or remove the reference")
        return issues

    def _collect_assigned_target_names(self, target: ast.AST, assigned: set[str]) -> None:
        if isinstance(target, ast.Name):
            assigned.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._collect_assigned_target_names(item, assigned)

    def _preloaded_analysis_names(self) -> set[str]:
        names = {
            "pd",
            "np",
            "plt",
            "sns",
            "build_sklearn_tree_artifact",
            "render_decision_tree_rules_figure",
            "nan",
            "NaN",
            "inf",
            "df",
            "df_numeric",
            "df_categorical",
        }
        names.update(dir(builtins))
        names.update(SAFE_BUILTINS)
        for dataset_name in self.workflow_state.get("csv_data", {}):
            clean = re.sub(r"[^a-zA-Z0-9_]", "_", str(dataset_name).replace(".csv", ""))
            names.update({f"df_{clean}", f"df_{clean}_numeric", f"df_{clean}_categorical"})
        return names

    def _fragile_metric_lookup_preflight_issues(self, code: str) -> list[str]:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []
        dataset_columns = {
            str(column)
            for df in self.workflow_state.get("csv_data", {}).values()
            for column in getattr(df, "columns", [])
        }
        literal_dict_keys_by_name: dict[str, set[str]] = {}
        string_constants_by_name: dict[str, str] = {}
        metric_summary_names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if isinstance(node.value, ast.Dict):
                keys = {
                    str(key.value)
                    for key in node.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
                if keys:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            literal_dict_keys_by_name.setdefault(target.id, set()).update(keys)
            elif isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        string_constants_by_name[target.id] = node.value.value.strip()
            elif self._looks_like_metric_summary_assignment(node.value):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        metric_summary_names.add(target.id)
        fragile_keys = {"median", "mean", "average", "avg", "count", "rate", "percentage", "percent"}
        issues: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript):
                continue
            key = self._constant_subscript_key(node.slice, string_constants_by_name)
            if key not in fragile_keys or key in dataset_columns:
                continue
            if isinstance(node.value, ast.Name) and node.value.id in metric_summary_names:
                continue
            if isinstance(node.value, ast.Name) and key in literal_dict_keys_by_name.get(node.value.id, set()):
                continue
            issues.append(
                f"avoid fragile metric lookup ['{key}']; use named aggregations, .get('{key}', fallback), or build records from observed grouped rows"
            )
        return sorted(set(issues))

    def _looks_like_metric_summary_assignment(self, node: ast.AST) -> bool:
        current = node
        while isinstance(current, ast.Call):
            func = current.func
            if isinstance(func, ast.Attribute) and func.attr in {"describe", "agg", "aggregate"}:
                return True
            current = func.value if isinstance(func, ast.Attribute) else current
            if current is node:
                break
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Call):
            return self._looks_like_metric_summary_assignment(node.value)
        return False

    def _constant_subscript_key(self, node: ast.AST, string_constants_by_name: dict[str, str] | None = None) -> str:
        string_constants_by_name = string_constants_by_name or {}
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value.strip()
        if isinstance(node, ast.Name):
            return string_constants_by_name.get(node.id, "")
        if isinstance(node, ast.Index):  # pragma: no cover - Python <3.9 compatibility
            return self._constant_subscript_key(node.value, string_constants_by_name)
        return ""

    def _uses_forbidden_tree_introspection(self, code: str) -> bool:
        tree_internal_patterns = (
            r"\bDecisionTree(?:Classifier|Regressor)\s*\.\s*tree_",
            r"\.\s*tree_\s*(?:\.|\[)",
            r"\.tree_\s*$",
            r"\.tree_\s*\n",
            r"\.tree_\s*,",
            r"\.tree_\s*\)",
        )
        if any(re.search(pattern, code, flags=re.MULTILINE) for pattern in tree_internal_patterns):
            return True
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return False
        class_names = self._decision_tree_class_aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "tree_":
                return True
            if isinstance(node, ast.Attribute) and node.attr in {"feature", "threshold", "value", "values"}:
                base = node.value
                if isinstance(base, ast.Attribute) and base.attr == "tree_":
                    return True
                if self._is_getattr_tree_reference(base, class_names):
                    return True
                if isinstance(base, ast.Name) and base.id in class_names:
                    return True
            if isinstance(node, ast.Call) and self._is_getattr_tree_reference(node, class_names):
                return True
        lowered = code.lower()
        if re.search(r"\b(?:children_left|children_right|thresholds?|features?|values?)\s*=\s*[^\\n]*\.tree_", lowered):
            return True
        return False

    def _is_getattr_tree_reference(self, node: ast.AST, class_names: set[str]) -> bool:
        if not isinstance(node, ast.Call) or self._call_name(node.func) != "getattr" or len(node.args) < 2:
            return False
        attr_name = self._constant_string(node.args[1])
        if attr_name == "tree_":
            return True
        if attr_name in {"feature", "threshold", "value", "values"}:
            return self._is_tree_class_or_property_reference(node.args[0], class_names)
        return False

    def _passes_tree_class_to_artifact_helper(self, code: str) -> bool:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return False
        class_names = self._decision_tree_class_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or self._call_name(node.func) != "build_sklearn_tree_artifact":
                continue
            if node.args and self._is_tree_class_or_property_reference(node.args[0], class_names):
                return True
            for keyword in node.keywords:
                if keyword.arg in {"model", "fitted_model_or_pipeline", "model_or_pipeline"}:
                    if self._is_tree_class_or_property_reference(keyword.value, class_names):
                        return True
        return False

    def _unsupported_tree_helper_keywords(self, code: str) -> list[str]:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []
        unsupported = {"model_or_pipeline", "tree", "tree_", "estimator", "fitted_tree"}
        names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or self._call_name(node.func) != "build_sklearn_tree_artifact":
                continue
            for keyword in node.keywords:
                if keyword.arg in unsupported:
                    names.add(str(keyword.arg))
        return sorted(names)

    def _is_tree_class_or_property_reference(self, node: ast.AST, class_names: set[str]) -> bool:
        if isinstance(node, ast.Name):
            return node.id in class_names
        if isinstance(node, ast.Attribute):
            if node.attr == "tree_":
                return True
            if node.attr in {"feature", "threshold", "value", "values"}:
                return self._is_tree_class_or_property_reference(node.value, class_names)
            return self._is_tree_class_or_property_reference(node.value, class_names)
        if isinstance(node, ast.Call) and self._is_getattr_tree_reference(node, class_names):
            return True
        return False

    def _constant_string(self, node: ast.AST) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value.strip()
        return ""

    def _decision_tree_class_aliases(self, tree: ast.AST) -> set[str]:
        aliases = {"DecisionTreeClassifier", "DecisionTreeRegressor"}
        changed = True
        while changed:
            changed = False
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "sklearn.tree":
                    for alias in node.names:
                        if alias.name in {"DecisionTreeClassifier", "DecisionTreeRegressor"}:
                            imported_name = alias.asname or alias.name
                            if imported_name not in aliases:
                                aliases.add(imported_name)
                                changed = True
                elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id in aliases:
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id not in aliases:
                            aliases.add(target.id)
                            changed = True
                elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Name) and node.value.id in aliases:
                    if isinstance(node.target, ast.Name) and node.target.id not in aliases:
                        aliases.add(node.target.id)
                        changed = True
        return aliases

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
        analysis_artifacts: Any,
    ) -> list[str]:
        missing: list[str] = []
        if not isinstance(analysis_summary, dict) or not analysis_summary:
            missing.append("analysis_summary")
        if not isinstance(business_findings, list) or not business_findings:
            missing.append("business_findings")
        if not isinstance(figure_captions, dict):
            missing.append("figure_captions")
        if not isinstance(analysis_artifacts, list):
            missing.append("analysis_artifacts")
        return missing


def prompt_user_data_description(input_fn: Callable[[str], str] = input) -> str:
    return input_fn(
        "Describe the dataset, business problem, or analysis goal (optional but recommended): "
    ).strip()


def prompt_decision_tree_target_column(
    available_columns: list[str],
    input_fn: Callable[[str], str] = input,
) -> str:
    if available_columns:
        print("Decision tree target column is optional.")
        print("Available columns:")
        for column in available_columns:
            print(f"  - {column}")
    target_column = input_fn(
        "Decision tree target column (leave blank to skip decision tree modeling): "
    ).strip()
    if not target_column:
        return ""
    if available_columns and target_column not in available_columns:
        print(f"Target column '{target_column}' was not found. Decision tree modeling will be skipped.")
        return ""
    return target_column


def run_non_interactive_workflow(
    config: RuntimeConfig,
    csv_paths: list[Path],
    user_data_description: str = "",
    decision_tree_target_column: str = "",
    *,
    workspace: Path | None = None,
    step_callback: StepCallback | None = None,
) -> dict[str, Any]:
    root = workspace or Path.cwd()
    selected_paths = [
        path if path.is_absolute() else root / path
        for path in csv_paths
    ]
    if not selected_paths:
        raise ValueError("At least one CSV path is required for a non-interactive workflow run.")

    orchestrator = MultiAgentOrchestrator(config, step_callback=step_callback)
    orchestrator.set_user_data_description(user_data_description)
    if not orchestrator.load_csv_paths(selected_paths):
        orchestrator.workflow_state["status"] = "error"
        orchestrator.workflow_state["failure"] = {
            "failed_step": 0,
            "failed_step_name": "CSV load",
            "error_message": "The selected CSV paths could not be loaded.",
            "partial_outputs_present": [],
            "recommended_retry_point": "CSV load",
        }
        return orchestrator.workflow_state
    if decision_tree_target_column and decision_tree_target_column not in orchestrator.available_columns():
        orchestrator.workflow_state["run_manifest"].setdefault("warnings", []).append(
            f"Decision tree target column '{decision_tree_target_column}' was not found; model skipped."
        )
        decision_tree_target_column = ""
    orchestrator.set_decision_tree_target_column(decision_tree_target_column)
    result = orchestrator.execute_workflow()
    result["cost_summary"] = orchestrator.openrouter_client.cost_tracker.report()
    return result


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

    user_data_description = prompt_user_data_description()
    if user_data_description:
        print("User context:")
        print(f"  {user_data_description}")
    target_column = prompt_decision_tree_target_column(
        [str(column) for path in csv_files for column in pd.read_csv(path, nrows=0).columns]
    )
    if target_column:
        print("Decision tree target:")
        print(f"  {target_column}")
    result = run_non_interactive_workflow(
        config,
        csv_files,
        user_data_description,
        decision_tree_target_column=target_column,
        workspace=root,
        step_callback=_print_step_update,
    )
    print()
    print(f"Workflow status: {result.get('status')}")
    for name, path in result.get("generated_reports", {}).items():
        print(f"Generated {name}: {path}")
    print()
    if result.get("cost_summary"):
        print(result["cost_summary"])
    return 0 if result.get("status") == "completed" else 1


def _print_step_update(step_number: int, step_name: str, status: str) -> None:
    print(format_step_update(step_number, step_name, status))
