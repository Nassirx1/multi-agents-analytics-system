from __future__ import annotations

import ast
import builtins
from concurrent.futures import ThreadPoolExecutor
import difflib
import hashlib
import importlib.util
import json
import logging
import os
import pickle
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
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
from .evidence import (
    artifact_dependency_hash,
    build_evidence_bundle,
    normalize_analysis_evidence,
    validate_and_sanitize_workflow,
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
from .runtime_config import RuntimeConfig, register_runtime_config
from .output_paths import OutputPath, coerce_output_path
from .run_checkpoints import load_run_checkpoint
from .serialization import json_dumps_safe, make_json_safe
from .workflow_steps import WORKFLOW_STEPS, format_step_update, workflow_steps_for

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
    def __init__(
        self,
        config: RuntimeConfig,
        step_callback: StepCallback | None = None,
        *,
        workspace: Path | None = None,
        create_run_directory: bool = True,
        output_path: OutputPath | str = OutputPath.ANALYTICS_REPORT,
    ) -> None:
        self.config = config
        self.output_path = coerce_output_path(output_path)
        self.workflow_steps = workflow_steps_for(self.output_path)
        self.openrouter_client = OpenRouterClient(
            config.openrouter_api_key,
            config.structured_model_name or config.model_name,
            request_timeout_seconds=config.agent_request_timeout_seconds,
            code_loop_timeout_seconds=config.code_loop_request_timeout_seconds,
        )
        shared_cost_tracker = self.openrouter_client.cost_tracker
        self.code_openrouter_client = OpenRouterClient(
            config.openrouter_api_key,
            config.code_model_name or config.model_name,
            request_timeout_seconds=config.agent_request_timeout_seconds,
            code_loop_timeout_seconds=config.code_loop_request_timeout_seconds,
            cost_tracker=shared_cost_tracker,
        )
        self.presentation_openrouter_client = OpenRouterClient(
            config.openrouter_api_key,
            config.presentation_model_name or config.model_name,
            request_timeout_seconds=config.agent_request_timeout_seconds,
            code_loop_timeout_seconds=config.code_loop_request_timeout_seconds,
            cost_tracker=shared_cost_tracker,
        )
        self.brave_client = (
            BraveSearchClient(config.brave_search_api_key)
            if self.output_path is OutputPath.ANALYTICS_REPORT and config.brave_search_api_key
            else None
        )
        self.shared_store = SharedContextStore()
        self.step_callback = step_callback
        self.workspace = Path(workspace).resolve() if workspace else None
        self.run_id = datetime.now().strftime("%Y%m%dT%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
        self.run_dir: Path | None = None
        self._checkpoint_lock = threading.RLock()
        self._step_started: dict[int, tuple[float, dict[str, Any], str]] = {}
        if self.workspace is not None and create_run_directory:
            self.run_dir = self.workspace / "runs" / self.run_id
            self.run_dir.mkdir(parents=True, exist_ok=False)
        kwargs = {"openrouter_client": self.openrouter_client, "shared_store": self.shared_store}
        code_kwargs = {"openrouter_client": self.code_openrouter_client, "shared_store": self.shared_store}
        presentation_kwargs = {
            "openrouter_client": self.presentation_openrouter_client,
            "shared_store": self.shared_store,
        }
        self.agents: dict[str, BaseAgent] = {
            "data_understander": DataUnderstanderAgent("Data Understander", "Senior Data Analyst", "data profiling", **kwargs),
        }
        if self.output_path is OutputPath.ANALYTICS_REPORT:
            self.agents.update(
                {
                    "market_researcher": MarketResearcherAgent("Market Researcher", "Market Research Specialist", "market trends", brave_client=self.brave_client, **kwargs),
                    "planner": PlannerAgent("Analysis Planner", "Senior Data Strategist", "analysis planning", **kwargs),
                    "coder": DataScientistCoderAgent("Data Scientist Coder", "Senior Data Scientist", "python analytics", **code_kwargs),
                    "reviewer": DataScientistReviewerAgent("Code Reviewer", "Senior Reviewer", "debugging and analytical review", **code_kwargs),
                    "business_translator": BusinessInsightsTranslatorAgent("Business Translator", "Business Intelligence Expert", "executive translation", **kwargs),
                    "decision_maker": DecisionMakerAgent("Decision Maker", "Senior Business Analyst", "decision recommendations", **kwargs),
                    "presentation_architect": PresentationArchitectAgent("Presentation Architect", "Presentation Consultant", "slide storytelling", **presentation_kwargs),
                }
            )
        for agent in self.agents.values():
            agent.set_shared_context(share_sample_values_with_model=config.share_sample_values_with_model)
        self.workflow_state: dict[str, Any] = {
            "csv_data": {},
            "user_data_description": "",
            "decision_tree_target_column": "",
            "agent_outputs": {},
            "analysis_results": {},
            "analysis_artifact_warnings": [],
            "current_step": 0,
            "total_steps": len(self.workflow_steps),
            "status": "initialized",
            "saved_figures": [],
            "generated_reports": {},
            "workflow_objective": self._build_workflow_objective(""),
            "failure": {},
            "run_manifest": {
                "run_id": self.run_id,
                "output_path": self.output_path.value,
                "run_directory": str(self.run_dir) if self.run_dir else "",
                "model_name": config.model_name,
                "agent_models": {
                    "structured": config.structured_model_name or config.model_name,
                    "code": config.code_model_name or config.model_name,
                    "presentation": config.presentation_model_name or config.model_name,
                },
                "datasets": [],
                "figures": [],
                "reports": {},
                "warnings": [],
                "agent_outputs": [],
                "final_code_present": False,
                "analysis_loop_iterations": 0,
                "analysis_retry_errors": [],
                "analysis_retry_context_log": [],
                "status": "initialized",
                "started_at": datetime.now().astimezone().isoformat(),
                "updated_at": datetime.now().astimezone().isoformat(),
                "step_events": [],
                "step_metrics": [],
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
        output_criterion = (
            "The self-contained HTML dashboard explains objective coverage, sources, and limitations."
            if self.output_path is OutputPath.HTML_DASHBOARD
            else "PDF and slide outputs explain objective coverage and limitations."
        )
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
                output_criterion,
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
                resolved_path = path.resolve(strict=True)
                file_size = resolved_path.stat().st_size
                if file_size > self.config.max_csv_bytes:
                    raise ValueError(
                        f"CSV '{resolved_path.name}' is {file_size:,} bytes; "
                        f"the configured limit is {self.config.max_csv_bytes:,} bytes."
                    )
                header = pd.read_csv(resolved_path, nrows=0)
                if len(header.columns) > self.config.max_csv_columns:
                    raise ValueError(
                        f"CSV '{resolved_path.name}' has {len(header.columns):,} columns; "
                        f"the configured limit is {self.config.max_csv_columns:,}."
                    )
                key = path.name
                if key in self.workflow_state["csv_data"]:
                    stable_suffix = hashlib.sha256(str(resolved_path).encode("utf-8")).hexdigest()[:10]
                    key = f"{path.stem}_{stable_suffix}{path.suffix}"
                dataframe = pd.read_csv(resolved_path, nrows=self.config.max_csv_rows + 1)
                if len(dataframe) > self.config.max_csv_rows:
                    raise ValueError(
                        f"CSV '{resolved_path.name}' exceeds the configured row limit "
                        f"of {self.config.max_csv_rows:,}."
                    )
                self.workflow_state["csv_data"][key] = dataframe
                digest = hashlib.sha256()
                with resolved_path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                self.workflow_state["run_manifest"]["datasets"].append(
                    {
                        "name": key,
                        "path": str(resolved_path),
                        "sha256": digest.hexdigest(),
                        "bytes": file_size,
                        "rows": int(dataframe.shape[0]),
                        "columns": int(dataframe.shape[1]),
                    }
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
        self.workflow_state["current_step"] = max(
            int(self.workflow_state.get("current_step") or 0), int(step_number)
        )
        step_name = self.workflow_steps[step_number - 1]
        now = datetime.now().astimezone().isoformat()
        usage = self.openrouter_client.cost_tracker.snapshot()
        event = {
            "step": int(step_number),
            "name": step_name,
            "status": str(status),
            "timestamp": now,
            "usage": usage,
        }
        self.workflow_state["run_manifest"].setdefault("step_events", []).append(event)
        normalized_status = str(status).strip().lower()
        if normalized_status.startswith(("running", "planning")) and step_number not in self._step_started:
            self._step_started[step_number] = (time.perf_counter(), usage, now)
        if normalized_status in {"done", "failed"} and step_number in self._step_started:
            started_monotonic, started_usage, started_at = self._step_started.pop(step_number)
            metric = {
                "step": int(step_number),
                "name": step_name,
                "status": normalized_status,
                "started_at": started_at,
                "ended_at": now,
                "duration_ms": max(0, round((time.perf_counter() - started_monotonic) * 1000)),
                "api_calls": max(0, usage["api_calls"] - started_usage["api_calls"]),
                "failed_calls": max(0, usage["failed_calls"] - started_usage["failed_calls"]),
                "prompt_tokens": max(0, usage["prompt_tokens"] - started_usage["prompt_tokens"]),
                "completion_tokens": max(0, usage["completion_tokens"] - started_usage["completion_tokens"]),
                "estimated_cost_usd": round(
                    max(0.0, usage["estimated_cost_usd"] - started_usage["estimated_cost_usd"]), 6
                ),
            }
            self.workflow_state["run_manifest"].setdefault("step_metrics", []).append(metric)
        self.workflow_state["run_manifest"]["updated_at"] = now
        if self.step_callback:
            self.step_callback(step_number, step_name, status)
        self._update_run_manifest()

    def _record_failure(self, exc: Exception) -> None:
        current_step = int(self.workflow_state.get("current_step") or 0)
        step_name = self.workflow_steps[current_step - 1] if 1 <= current_step <= len(self.workflow_steps) else "unknown"
        traceback_text = traceback.format_exc()
        self.workflow_state["failure"] = {
            "failed_step": current_step,
            "failed_step_name": step_name,
            "error_message": str(exc),
            "traceback": traceback_text,
            "partial_outputs_present": sorted(self.workflow_state.get("agent_outputs", {}).keys()),
            "recommended_retry_point": step_name,
        }
        if hasattr(exc, "code"):
            self.workflow_state["failure"]["error_code"] = str(getattr(exc, "code"))
        if hasattr(exc, "retryable"):
            self.workflow_state["failure"]["retryable"] = bool(getattr(exc, "retryable"))
        if getattr(exc, "details", None):
            self.workflow_state["failure"]["details"] = dict(getattr(exc, "details"))

    def execute_workflow(self) -> dict[str, Any]:
        if self.output_path is OutputPath.HTML_DASHBOARD:
            return self._execute_html_dashboard_workflow()

        outputs = self.workflow_state["agent_outputs"]
        self.workflow_state["status"] = "running"
        self.workflow_state["failure"] = {}
        try:
            data_insights = outputs.get("data_understander")
            if not isinstance(data_insights, dict):
                self._set_step(1, "running")
                data_insights = self.agents["data_understander"].execute(self.workflow_state["csv_data"])
                outputs["data_understander"] = data_insights
                self._set_step(1, "done")
            else:
                self._set_step(1, "cached")

            market_insights = outputs.get("market_researcher")
            if not isinstance(market_insights, dict):
                if self._market_research_is_enabled():
                    self._set_step(2, "running")
                    market_insights = self.agents["market_researcher"].execute(data_insights)
                    self._set_step(2, "done")
                else:
                    market_insights = {
                        "industry_overview": "External market research was intentionally skipped for this run.",
                        "market_findings": [],
                        "key_trends": [],
                        "opportunities": [],
                        "sources_cited": [],
                        "search_queries": [],
                        "market_evidence_level": "disabled",
                    }
                    self._set_step(2, "skipped")
                outputs["market_researcher"] = market_insights
            else:
                self._set_step(2, "cached")

            analysis_plan = outputs.get("planner")
            if not isinstance(analysis_plan, dict):
                self._set_step(3, "running")
                analysis_plan = self.agents["planner"].execute(data_insights, market_insights)
                outputs["planner"] = analysis_plan
                self._set_step(3, "done")
            else:
                self._set_step(3, "cached")

            self.agents["coder"].context["data_understanding"] = data_insights
            self.agents["reviewer"].context["data_understanding"] = data_insights
            final_code = str(outputs.get("final_code", "")).strip()
            reusable_analysis = (
                bool(final_code)
                and self.workflow_state.get("analysis_results", {}).get("execution_status") == "success"
            )
            if not reusable_analysis:
                final_code = self._coding_loop(analysis_plan)
                outputs["final_code"] = final_code
            else:
                self._set_step(4, "cached")
                self._set_step(5, "cached")
            if self.workflow_state["analysis_results"].get("execution_status") != "success":
                self.workflow_state["analysis_results"] = self._execute_code(final_code)
            final_artifact_issues = self._analysis_output_issues(self.workflow_state["analysis_results"])
            self.workflow_state["analysis_artifact_warnings"] = final_artifact_issues
            if self.workflow_state["analysis_results"].get("execution_status") != "success":
                execution_error = self.workflow_state["analysis_results"].get("error", "Unknown execution error.")
                raise RuntimeError(f"Final analysis code failed to execute: {execution_error}")

            # Build the canonical evidence layer before narrative agents run so they receive
            # compact, corrected, traceable facts instead of truncated raw execution output.
            self.workflow_state["evidence_bundle"] = build_evidence_bundle(self.workflow_state)

            business = outputs.get("business_translator")
            if not isinstance(business, dict):
                self._set_step(6, "running")
                business = self.agents["business_translator"].execute(
                    self.workflow_state["analysis_results"],
                    data_insights,
                    market_insights,
                    self.workflow_state["evidence_bundle"],
                )
                outputs["business_translator"] = business
                self._set_step(6, "done")
            else:
                self._set_step(6, "cached")

            decision = outputs.get("decision_maker")
            if not isinstance(decision, dict):
                self._set_step(7, "running")
                decision = self.agents["decision_maker"].execute(
                    outputs,
                    self.workflow_state["analysis_results"],
                    business,
                    self.workflow_state["evidence_bundle"],
                )
                outputs["decision_maker"] = decision
                self._set_step(7, "done")
            else:
                self._set_step(7, "cached")

            # Revalidate cached or newly generated narrative outputs. This attaches evidence
            # IDs, softens unsupported language, and removes invented tree thresholds.
            validate_and_sanitize_workflow(self.workflow_state)

            report_dir = self._ensure_run_directory()
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="analytics-export") as executor:
                pdf_future = executor.submit(self._generate_pdf_export, report_dir)
                slides_future = executor.submit(self._generate_slide_export, report_dir, outputs)
                pdf_future.result()
                slides_future.result()
            self._validate_final_artifact_consistency(report_dir)
            self.workflow_state["current_step"] = 9
            self.workflow_state["status"] = "completed"
            self._update_run_manifest(final_code=final_code)
        except Exception as exc:
            self._logger.error("Workflow failed: %s", exc)
            self._logger.error(traceback.format_exc())
            self._record_failure(exc)
            self.workflow_state["status"] = "error"
            self._update_run_manifest()
        return self.workflow_state

    def _execute_html_dashboard_workflow(self) -> dict[str, Any]:
        """Run the minimal HTML-dashboard branch without report/export agents."""
        outputs = self.workflow_state["agent_outputs"]
        self.workflow_state["status"] = "running"
        self.workflow_state["failure"] = {}
        try:
            data_insights = outputs.get("data_understander")
            if not isinstance(data_insights, dict):
                self._set_step(1, "running")
                data_insights = self.agents["data_understander"].execute(self.workflow_state["csv_data"])
                outputs["data_understander"] = data_insights
                self._set_step(1, "done")
            else:
                self._set_step(1, "cached")

            from .html_dashboard.workflow import HTMLDashboardWorkflow

            report_dir = self._ensure_run_directory()
            dashboard_result = HTMLDashboardWorkflow(
                self.config,
                self.openrouter_client,
                self.run_id,
                report_dir,
                self._set_step,
            ).run(self.workflow_state)
            self.workflow_state["html_dashboard_result"] = dashboard_result
            self.workflow_state["generated_reports"].update(
                {
                    "html_dashboard": dashboard_result["html"],
                    "dashboard_project": dashboard_result["project"],
                    "dashboard_qa_receipt": dashboard_result["qa_receipt"],
                }
            )
            self.workflow_state["current_step"] = len(self.workflow_steps)
            self.workflow_state["status"] = "completed"
            self._update_run_manifest(final_code="")
        except Exception as exc:
            self._logger.error("HTML dashboard workflow failed: %s", exc)
            self._logger.error(traceback.format_exc())
            self._record_failure(exc)
            self.workflow_state["status"] = "error"
            self._update_run_manifest()
        return self.workflow_state

    def _generate_pdf_export(self, report_dir: Path) -> None:
        pdf_path = self.workflow_state["generated_reports"].get("pdf", "")
        dependency_hash = artifact_dependency_hash(self.workflow_state, "pdf")
        dependencies = self.workflow_state.setdefault("artifact_dependencies", {})
        if pdf_path and Path(str(pdf_path)).is_file() and dependencies.get("pdf") == dependency_hash:
            self._set_step(8, "cached")
            return
        self._set_step(8, "running")
        self.workflow_state["generated_reports"]["pdf"] = generate_pdf_report(
            self.workflow_state,
            str(report_dir / "analytics_report.pdf"),
        )
        dependencies["pdf"] = dependency_hash
        self._set_step(8, "done")

    def _generate_slide_export(self, report_dir: Path, outputs: dict[str, Any]) -> None:
        slide_path = self.workflow_state["generated_reports"].get("slide_deck", "")
        dependency_hash = artifact_dependency_hash(self.workflow_state, "slides")
        dependencies = self.workflow_state.setdefault("artifact_dependencies", {})
        if slide_path and Path(str(slide_path)).is_file() and dependencies.get("slides") == dependency_hash:
            self._set_step(9, "cached")
            return
        if isinstance(self.workflow_state.get("ppt_mcp_deck_spec"), dict):
            self._set_step(9, "planning")
            outputs["presentation_architect"] = {
                "design_source": "ppt_mcp",
                "deck_title": self.workflow_state["ppt_mcp_deck_spec"].get("deck_title", ""),
                "slides": self.workflow_state["ppt_mcp_deck_spec"].get("slides", []),
            }
        elif not self.config.presentation_architect_enabled:
            outputs["presentation_architect"] = {
                "design_source": "deterministic_hybrid",
                "deck_title": "",
                "slides": [],
            }
        elif not isinstance(outputs.get("presentation_architect"), dict):
            self._set_step(9, "planning")
            try:
                outputs["presentation_architect"] = self.agents["presentation_architect"].execute(
                    self.workflow_state
                )
            except (RuntimeError, ValueError) as exc:
                self._logger.warning(
                    "Presentation architect plan failed; using validated deterministic deck fallback: %s", exc
                )
                outputs["presentation_architect"] = {
                    "design_source": "deterministic_fallback",
                    "deck_title": "",
                    "slides": [],
                    "planning_warning": str(exc),
                }
                self.workflow_state["run_manifest"].setdefault("warnings", []).append(
                    "Presentation architect returned an invalid plan; deterministic deck fallback used."
                )
        self._set_step(9, "running")
        self.workflow_state["_presentation_openrouter_client"] = self.presentation_openrouter_client
        try:
            self.workflow_state["generated_reports"]["slide_deck"] = generate_slide_deck(
                self.workflow_state,
                str(report_dir / "analytics_report.pptx"),
                runtime_config=self.config,
            )
            dependencies["slides"] = dependency_hash
        finally:
            self.workflow_state.pop("_presentation_openrouter_client", None)
        self._set_step(9, "done")

    def _market_research_is_enabled(self) -> bool:
        if not self.config.market_research_enabled:
            return False
        description = str(self.workflow_state.get("user_data_description", "")).lower()
        skip_phrases = {
            "dataset only",
            "internal only",
            "no external research",
            "without external research",
            "skip market research",
        }
        return not any(phrase in description for phrase in skip_phrases)

    def _validate_final_artifact_consistency(self, report_dir: Path) -> None:
        expected_hash = str((self.workflow_state.get("evidence_bundle", {}) or {}).get("bundle_hash", ""))
        observed: dict[str, str] = {}
        for label, path in (
            ("pdf", report_dir / "report_outline.json"),
            ("slides", report_dir / "slide_plan.json"),
        ):
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if label == "slides":
                observed[label] = str((payload.get("metadata", {}) or {}).get("evidence_bundle_hash", ""))
            else:
                observed[label] = str(payload.get("evidence_bundle_hash", ""))
        mismatches = [label for label, value in observed.items() if expected_hash and value != expected_hash]
        receipt = {
            "status": "failed" if mismatches else "passed",
            "expected_evidence_bundle_hash": expected_hash,
            "artifact_hashes": observed,
            "mismatches": mismatches,
        }
        self.workflow_state["final_output_consistency"] = receipt
        if mismatches:
            raise RuntimeError(
                "Final PDF/slide evidence hashes do not match the canonical evidence bundle: "
                + ", ".join(mismatches)
            )

    def _ensure_run_directory(self) -> Path:
        if self.run_dir is None:
            base = self.workspace / "runs" if self.workspace else Path(tempfile.gettempdir()) / "analytics_workflow_runs"
            self.run_dir = base / self.run_id
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self.workflow_state["run_manifest"]["run_directory"] = str(self.run_dir)
        return self.run_dir

    def _persist_run_artifacts(self, final_code: str = "") -> None:
        with self._checkpoint_lock:
            self._persist_run_artifacts_unlocked(final_code)

    def _persist_run_artifacts_unlocked(self, final_code: str = "") -> None:
        run_dir = self._ensure_run_directory()
        final_code = final_code or str(self.workflow_state.get("agent_outputs", {}).get("final_code", ""))
        if final_code:
            code_path = run_dir / "final_analysis.py"
            code_path.write_text(final_code, encoding="utf-8")
            self.workflow_state.setdefault("generated_reports", {})["final_analysis_code"] = str(code_path)
        manifest_path = run_dir / "run_manifest.json"
        self.workflow_state.setdefault("generated_reports", {})["run_manifest"] = str(manifest_path)
        agent_outputs_path = run_dir / "agent_outputs.json"
        agent_outputs_path.write_text(
            json.dumps(make_json_safe(self.workflow_state.get("agent_outputs", {})), indent=2, default=str),
            encoding="utf-8",
        )
        self.workflow_state.setdefault("generated_reports", {})["agent_outputs"] = str(agent_outputs_path)
        if self.workflow_state.get("analysis_results"):
            analysis_results_path = run_dir / "analysis_results.json"
            analysis_results_path.write_text(
                json.dumps(make_json_safe(self.workflow_state["analysis_results"]), indent=2, default=str),
                encoding="utf-8",
            )
            self.workflow_state.setdefault("generated_reports", {})["analysis_results"] = str(analysis_results_path)
        for state_key, filename in (
            ("evidence_bundle", "evidence_bundle.json"),
            ("quality_receipt", "quality_receipt.json"),
            ("pdf_quality_receipt", "pdf_quality_receipt.json"),
            ("final_output_consistency", "final_output_consistency.json"),
            ("artifact_dependencies", "artifact_dependencies.json"),
        ):
            payload = self.workflow_state.get(state_key)
            if not payload:
                continue
            artifact_path = run_dir / filename
            artifact_path.write_text(
                json.dumps(make_json_safe(payload), indent=2, default=str),
                encoding="utf-8",
            )
            self.workflow_state.setdefault("generated_reports", {})[state_key] = str(artifact_path)
        delivery_dir = run_dir / "delivery"
        delivery_dir.mkdir(exist_ok=True)
        authoritative: dict[str, str] = {}
        for report_key, filename in (
            ("pdf", "analytics_report.pdf"),
            ("slide_deck", "analytics_report.pptx"),
            ("evidence_bundle", "evidence_bundle.json"),
            ("quality_receipt", "quality_receipt.json"),
            ("pdf_quality_receipt", "pdf_quality_receipt.json"),
            ("final_output_consistency", "final_output_consistency.json"),
        ):
            source_text = str(self.workflow_state.get("generated_reports", {}).get(report_key, ""))
            source = Path(source_text) if source_text else None
            if source is None or not source.is_file():
                continue
            target = delivery_dir / filename
            shutil.copy2(source, target)
            authoritative[report_key] = str(target)
        delivery_manifest = {
            "run_id": self.run_id,
            "authoritative": authoritative,
            "quality_status": (self.workflow_state.get("quality_receipt", {}) or {}).get("status"),
            "evidence_bundle_hash": (self.workflow_state.get("evidence_bundle", {}) or {}).get("bundle_hash"),
            "generated_at": datetime.now().astimezone().isoformat(),
        }
        delivery_manifest_path = delivery_dir / "delivery_manifest.json"
        delivery_manifest_path.write_text(json.dumps(delivery_manifest, indent=2), encoding="utf-8")
        self.workflow_state.setdefault("generated_reports", {})["delivery_dir"] = str(delivery_dir)
        self.workflow_state.setdefault("generated_reports", {})["delivery_manifest"] = str(delivery_manifest_path)
        self.workflow_state.setdefault("run_manifest", {})["reports"] = dict(self.workflow_state.get("generated_reports", {}))
        manifest_path.write_text(
            json.dumps(make_json_safe(self.workflow_state.get("run_manifest", {})), indent=2, default=str),
            encoding="utf-8",
        )
        shutil.copy2(manifest_path, delivery_dir / "run_manifest.json")

    def _update_run_manifest(self, final_code: str = "") -> None:
        existing_warnings = list(self.workflow_state["run_manifest"].get("warnings", []))
        artifact_warnings = list(self.workflow_state.get("analysis_artifact_warnings", []))
        now = datetime.now().astimezone()
        status = str(self.workflow_state.get("status", "initialized"))
        lifecycle: dict[str, Any] = {}
        if status in {"completed", "error"}:
            lifecycle["completed_at"] = now.isoformat()
            try:
                started = datetime.fromisoformat(str(self.workflow_state["run_manifest"].get("started_at", "")))
                lifecycle["run_duration_ms"] = max(0, round((now - started).total_seconds() * 1000))
            except (TypeError, ValueError):
                pass
        self.workflow_state["run_manifest"].update(
            {
                "figures": list(self.workflow_state.get("saved_figures", [])),
                "reports": dict(self.workflow_state.get("generated_reports", {})),
                "warnings": list(dict.fromkeys(str(item) for item in existing_warnings + artifact_warnings)),
                "agent_outputs": sorted(self.workflow_state.get("agent_outputs", {}).keys()),
                "final_code_present": bool(final_code or self.workflow_state.get("agent_outputs", {}).get("final_code")),
                "presentation_backend_used": self.workflow_state.get("presentation_backend_used"),
                "presentation_backend_log": make_json_safe(
                    self.workflow_state.get("presentation_backend_log", [])
                ),
                "status": status,
                "updated_at": now.isoformat(),
                "current_step": int(self.workflow_state.get("current_step") or 0),
                "usage": self.openrouter_client.cost_tracker.snapshot(),
                "workflow_inputs": {
                    "user_data_description": self.workflow_state.get("user_data_description", ""),
                    "decision_tree_target_column": self.workflow_state.get("decision_tree_target_column", ""),
                },
                "evidence_bundle_hash": (self.workflow_state.get("evidence_bundle", {}) or {}).get("bundle_hash"),
                "quality_status": (self.workflow_state.get("quality_receipt", {}) or {}).get("status"),
                "final_output_consistency": make_json_safe(self.workflow_state.get("final_output_consistency", {})),
                "artifact_dependencies": dict(self.workflow_state.get("artifact_dependencies", {})),
                "failure": make_json_safe(self.workflow_state.get("failure", {})),
                **lifecycle,
            }
        )
        self._persist_run_artifacts(final_code=final_code)

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
            candidate_dir = self._ensure_run_directory() / "code_candidates"
            candidate_dir.mkdir(parents=True, exist_ok=True)
            (candidate_dir / f"iteration_{iteration + 1}.py").write_text(current_code, encoding="utf-8")

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
                    code=current_code,
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
                    self.workflow_state["agent_outputs"]["final_code_review"] = {
                        "quality_score": 10,
                        "decision": "APPROVE",
                        "critical_issues": [],
                        "improvements": [],
                        "summary": (
                            "Approved by the deterministic code-review gate: safety, syntax, isolated execution, "
                            "required output contracts, objective coverage, and analytical artifacts all passed."
                        ),
                        "review_mode": "deterministic_contract",
                    }
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
                    code=current_code,
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
                    # Model review cannot override deterministic evidence-contract failures.
                    # Preserve its feedback, but require another code attempt until the
                    # reported artifact issues are actually resolved.
                    decision = "REVISE"
                    review["decision"] = "REVISE"
                    review.setdefault("critical_issues", []).extend(artifact_issues)
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
        code: str = "",
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
                entry["traceback_tail"] = self._compact_context_text(traceback_text, limit=1200)
            failure_context = self._code_failure_context(code, str(traceback_text or ""), str(error or ""))
            if failure_context:
                entry["failing_code_context"] = failure_context
        if review:
            entry["review_decision"] = self._compact_context_text(review.get("decision", ""))
            entry["review_summary"] = self._compact_context_text(review.get("summary", ""), limit=320)
            critical = review.get("critical_issues") or []
            if isinstance(critical, list):
                entry["review_critical_issues"] = [
                    self._compact_context_text(item, limit=220) for item in critical[:3]
                ]
        log.append(entry)
        del log[:-4]
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
                "revision_contract": [
                    "Fix every recorded failure before adding optional analysis.",
                    "Do not repeat imports, label lookups, tree introspection, or empty-array indexing that already failed.",
                    "Use the exact failing source context below; replace the faulty expression rather than rewriting it unchanged.",
                ],
                "recent_failures_newest_first": list(reversed(entries[-4:])),
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
            if re.fullmatch(r"'[01]'", message):
                guidance.insert(
                    0,
                    "Fix binary-label key types: pandas groupby/value_counts dictionaries preserve numeric labels as integers. "
                    "Do not read ['0'] or ['1']; derive observed labels from the grouped index or use guarded .get(1, .get('1', fallback)) and .get(0, .get('0', fallback)).",
                )
        if "index 0 is out of bounds" in message or "single positional indexer is out-of-bounds" in message:
            guidance.append(
                "Fix empty filtered selections: never use .values[0], .iloc[0], or .index[0] after filtering for a named category unless emptiness was checked. "
                "Do not invent literal categories; rank observed grouped rows, use .head(1), and branch when the result is empty."
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
            "model_type='classification', train_score=train_score, test_score=test_score, baseline_score=baseline_score, "
            "balanced_accuracy=balanced_accuracy, precision=precision, recall=recall, f1=f1, "
            "confusion_matrix=confusion_matrix.tolist(), positive_test_support=int((y_test == positive_class).sum()), "
            "cross_validation=cross_validation_summary). "
            "Then call render_decision_tree_rules_figure(decision_tree_artifact, 'decision_tree_rules.png'), "
            "append decision_tree_artifact to analysis_artifacts, and set figure_captions['decision_tree_rules.png']. "
            "For imbalanced classification, also record balanced_accuracy, positive-class precision, recall, F1, "
            "confusion_matrix, positive test support, and cross-validation status in analysis_summary['decision_tree_metrics']. "
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
            imbalance = self._analysis_target_imbalance_ratio(analysis_summary)
            if imbalance is not None and imbalance < 0.20:
                metric_payload = analysis_summary.get("decision_tree_metrics", {}) if isinstance(analysis_summary, dict) else {}
                metric_text = " ".join(str(key).lower() for key in metric_payload.keys()) if isinstance(metric_payload, dict) else ""
                missing_sensitive = [
                    label
                    for label, aliases in {
                        "balanced_accuracy": ("balanced_accuracy", "balanced accuracy"),
                        "precision": ("precision",),
                        "recall": ("recall", "sensitivity"),
                        "f1": ("f1", "f1_score"),
                        "confusion_matrix": ("confusion",),
                        "positive_test_support": ("positive_test_support", "test_support", "positive support"),
                    }.items()
                    if not any(alias in metric_text for alias in aliases)
                ]
                if missing_sensitive:
                    issues.append(
                        "Imbalanced classification is missing class-sensitive validation metrics: "
                        + ", ".join(missing_sensitive)
                        + ". Accuracy alone is not decision-safe."
                    )
        if figures and not structured_artifacts:
            issues.append("Analysis produced PNG figures but no structured chart artifacts; slide visuals cannot rebuild charts from images.")
        if not isinstance(analysis_summary, dict) or len(analysis_summary) < 2:
            issues.append("analysis_summary is missing or too small to support business reporting.")
        elif not self._has_numeric_evidence(analysis_summary):
            issues.append("analysis_summary does not contain clear numeric evidence.")
        if not isinstance(business_findings, list) or len(business_findings) < 2:
            issues.append("business_findings are missing or too small to support business reporting.")
        consistency_copy = make_json_safe(execution)
        corrections = normalize_analysis_evidence(consistency_copy) if isinstance(consistency_copy, dict) else []
        corrections = [
            item for item in corrections
            if item.get("reason") == "recomputed_from_structured_correlation_rows"
        ]
        if corrections:
            issues.append(
                "Structured artifact prose contradicts attached chart/model data: "
                + "; ".join(
                    f"{item.get('artifact_id')} {item.get('field')}" for item in corrections[:4]
                )
                + ". Recompute the claim from the structured values instead of hardcoding it."
            )
        issues.extend(self._planned_method_coverage_issues(execution))

        missing_captions = [figure for figure in figures if not _stringify(figure_captions.get(figure, "")).strip()]
        if missing_captions:
            issues.append(f"Missing figure captions for: {', '.join(missing_captions[:4])}.")

        return issues

    def _planned_method_coverage_issues(self, execution: dict[str, Any]) -> list[str]:
        planner = self.workflow_state.get("agent_outputs", {}).get("planner", {}) or {}
        planned_text = json.dumps(
            {
                "statistical_methods": planner.get("statistical_methods", []),
                "analysis_rules": planner.get("analysis_rules", []),
                "success_metrics": planner.get("success_metrics", []),
            },
            default=str,
        ).lower()
        if not planned_text.strip("{}[] "):
            return []
        produced_text = json.dumps(
            {
                "summary": execution.get("analysis_summary", {}),
                "artifacts": execution.get("analysis_artifacts", []),
                "findings": execution.get("business_findings", []),
            },
            default=str,
        ).lower()
        checks = {
            "chi-square association test": (("chi-square", "chi square", "chi2"), ("chi_square", "chi-square", "chi2")),
            "numeric group comparison test": (("t-test", "t test", "mann-whitney"), ("t_test", "t-test", "mann", "group_test")),
            "cross-validation": (("cross-validation", "cross validation", "5-fold"), ("cross_validation", "cross-validation", "cv_")),
            "AUC": (("auc", "roc"), ("auc", "roc")),
        }
        missing: list[str] = []
        for label, (plan_tokens, output_tokens) in checks.items():
            if any(token in planned_text for token in plan_tokens) and not any(token in produced_text for token in output_tokens):
                missing.append(label)
        return [
            "Analysis plan promised methods that were neither produced nor explicitly omitted: " + ", ".join(missing) + "."
        ] if missing else []

    def _analysis_target_imbalance_ratio(self, analysis_summary: Any) -> float | None:
        if not isinstance(analysis_summary, dict):
            return None
        value = analysis_summary.get("target_imbalance_ratio")
        try:
            if value not in (None, ""):
                return float(value)
        except (TypeError, ValueError):
            pass
        distribution = analysis_summary.get("target_distribution")
        if not isinstance(distribution, dict) or len(distribution) < 2:
            return None
        try:
            counts = sorted(float(item) for item in distribution.values())
        except (TypeError, ValueError):
            return None
        return counts[0] / counts[-1] if counts[-1] else None

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
        caveat_language = any(
            phrase in text
            for phrase in (
                "explanatory",
                "exploratory screening",
                "screening only",
                "not predictive",
                "no predictive lift",
            )
        )
        comparison_language = any(
            phrase in text
            for phrase in ("baseline", "production", "deployment", "predictive lift")
        )
        return not (caveat_language and comparison_language)

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
        dependency_result = self._missing_allowed_import_result(code)
        if dependency_result:
            return dependency_result

        run_dir = self._ensure_run_directory()
        execution_dir = run_dir / "analysis_attempts" / (
            datetime.now().strftime("%Y%m%dT%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
        )

        execution_dir.mkdir(parents=True, exist_ok=False)
        input_path = execution_dir / ".worker_input.pkl"
        output_path = execution_dir / ".worker_output.json"
        payload = {
            "code": code,
            "csv_data": self.workflow_state.get("csv_data", {}),
            "decision_tree_target_column": self.workflow_state.get("decision_tree_target_column", ""),
            "output_dir": str(execution_dir),
        }
        with input_path.open("wb") as handle:
            pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)

        project_root = Path(__file__).resolve().parents[1]
        worker_command = (
            "import runpy,sys;"
            f"sys.path.insert(0,{str(project_root)!r});"
            "runpy.run_module('analytics_workflow.analysis_worker',run_name='__main__')"
        )
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "WINDIR": os.environ.get("WINDIR", ""),
            "TEMP": str(execution_dir),
            "TMP": str(execution_dir),
            "HOME": str(execution_dir),
            "USERPROFILE": str(execution_dir),
            "MPLCONFIGDIR": str(execution_dir / ".matplotlib"),
            "PYTHONHASHSEED": "0",
            "NO_PROXY": "*",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
        }
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-c", worker_command, str(input_path), str(output_path)],
                cwd=execution_dir,
                env=environment,
                capture_output=True,
                text=True,
                timeout=self.config.analysis_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "execution_status": "failed",
                "error": f"Analysis execution exceeded {self.config.analysis_timeout_seconds} seconds and was terminated.",
                "traceback": "",
                "timed_out": True,
            }
        finally:
            try:
                input_path.unlink(missing_ok=True)
            except OSError:
                pass

        if not output_path.exists():
            return {
                "execution_status": "failed",
                "error": f"Analysis worker exited with code {completed.returncode} without a result.",
                "traceback": (completed.stderr or completed.stdout or "")[-4000:],
            }
        try:
            result = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "execution_status": "failed",
                "error": f"Analysis worker returned an invalid result: {exc}",
                "traceback": (completed.stderr or completed.stdout or "")[-4000:],
            }
        finally:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                pass
        if not isinstance(result, dict):
            return {"execution_status": "failed", "error": "Analysis worker result was not an object.", "traceback": ""}
        result["execution_directory"] = str(execution_dir)
        return result

    def _code_failure_context(self, code: str, traceback_text: str, error_text: str) -> dict[str, Any]:
        if not code:
            return {}
        matches = re.findall(r'File "<string>", line (\d+)', traceback_text)
        line_number = int(matches[-1]) if matches else 0
        lines = code.splitlines()
        if line_number <= 0 or line_number > len(lines):
            return {"error_type": error_text[:160]}
        start = max(1, line_number - 3)
        end = min(len(lines), line_number + 3)
        snippet = [f"{index}: {lines[index - 1]}" for index in range(start, end + 1)]
        return {
            "line": line_number,
            "error_type": error_text[:200],
            "source": snippet,
        }

    def _execute_code_in_process(self, code: str) -> dict[str, Any]:
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
            run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:6]
            saved_figure_paths = []
            figure_name_map = {}
            execution_root = Path.cwd().resolve()

            def resolve_figure_path(filename: Any) -> Any:
                if not isinstance(filename, (str, Path)):
                    raise ValueError("Figure output must be a file path.")
                raw_path = Path(filename)
                raw_name = str(raw_path)
                if raw_path.suffix.lower() != ".png":
                    raise ValueError("Generated figures must use the .png extension.")
                if raw_path.is_absolute() and raw_path.parent.resolve() == execution_root and run_stamp in raw_path.stem:
                    return str(raw_path)
                safe_stem = re.sub(r"[^a-zA-Z0-9_-]", "_", raw_path.stem).strip("_-") or "figure"
                resolved_path = execution_root / f"{safe_stem}_{run_stamp}.png"
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
        self._enrich_classification_validation(analysis_summary, analysis_artifacts, exec_globals)
        self._ensure_decision_tree_figures(analysis_artifacts, figure_captions, saved_figure_paths, run_stamp)
        figures = [path for path in saved_figure_paths if os.path.exists(path)]
        analysis_artifacts = self._confine_artifact_paths(analysis_artifacts, figures)
        chart_specs = self._confine_artifact_paths(chart_specs, figures)
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

    def _enrich_classification_validation(
        self,
        analysis_summary: dict[str, Any],
        artifacts: list[dict[str, Any]],
        runtime_values: dict[str, Any],
    ) -> None:
        tree_artifacts = [
            item for item in artifacts
            if isinstance(item, dict) and str(item.get("chart_type", "")).lower() == "decision_tree"
        ]
        if not tree_artifacts:
            return
        y_test = runtime_values.get("y_test")
        y_pred = runtime_values.get("y_pred_test")
        if y_pred is None:
            y_pred = runtime_values.get("y_test_pred")
        if y_test is None or y_pred is None:
            return
        try:
            from sklearn.metrics import (
                balanced_accuracy_score,
                confusion_matrix,
                f1_score,
                precision_score,
                recall_score,
                roc_auc_score,
            )

            observed = pd.Series(y_test)
            predicted = pd.Series(y_pred)
            classes = sorted(observed.dropna().unique().tolist())
            if len(classes) != 2:
                return
            positive = classes[-1]
            metrics = analysis_summary.setdefault("decision_tree_metrics", {})
            metrics.update(
                {
                    "balanced_accuracy": round(float(balanced_accuracy_score(observed, predicted)), 4),
                    "precision": round(float(precision_score(observed, predicted, pos_label=positive, zero_division=0)), 4),
                    "recall": round(float(recall_score(observed, predicted, pos_label=positive, zero_division=0)), 4),
                    "f1": round(float(f1_score(observed, predicted, pos_label=positive, zero_division=0)), 4),
                    "confusion_matrix": confusion_matrix(observed, predicted, labels=classes).tolist(),
                    "positive_test_support": int((observed == positive).sum()),
                }
            )
            model = next(
                (
                    runtime_values.get(name)
                    for name in ("pipeline", "model", "classifier", "clf", "tree_model")
                    if runtime_values.get(name) is not None and hasattr(runtime_values.get(name), "predict")
                ),
                None,
            )
            if model is not None and hasattr(model, "predict_proba"):
                try:
                    probabilities = model.predict_proba(runtime_values.get("X_test"))[:, -1]
                    metrics["auc_roc"] = round(float(roc_auc_score(observed, probabilities)), 4)
                except Exception:
                    pass
            X_all = runtime_values.get("X")
            y_all = runtime_values.get("y")
            if model is not None and X_all is not None and y_all is not None:
                try:
                    from sklearn.model_selection import StratifiedKFold, cross_val_score

                    all_target = pd.Series(y_all)
                    minority_support = int(all_target.value_counts().min())
                    folds = min(5, minority_support)
                    if folds >= 2:
                        splitter = StratifiedKFold(n_splits=folds, shuffle=True, random_state=42)
                        scores = cross_val_score(model, X_all, y_all, cv=splitter, scoring="balanced_accuracy")
                        metrics["cross_validation"] = {
                            "folds": folds,
                            "metric": "balanced_accuracy",
                            "mean": round(float(np.mean(scores)), 4),
                            "std": round(float(np.std(scores)), 4),
                        }
                except Exception as exc:
                    metrics["cross_validation"] = {"status": "not_completed", "reason": str(exc)[:180]}
            for artifact in tree_artifacts:
                data = artifact.setdefault("data", {})
                if isinstance(data, dict):
                    data.update(metrics)
                    data["positive_class_rate"] = round(float((observed == positive).mean()), 4)
        except Exception:
            return

    def _confine_artifact_paths(self, value: Any, allowed_paths: list[str]) -> Any:
        allowed = {str(Path(path).resolve()) for path in allowed_paths if path}
        path_keys = {"image_path", "visual_path", "fallback_path"}

        def visit(item: Any) -> Any:
            if isinstance(item, dict):
                cleaned: dict[str, Any] = {}
                for key, nested in item.items():
                    if key in path_keys and nested:
                        try:
                            resolved = str(Path(str(nested)).resolve())
                        except OSError:
                            resolved = ""
                        cleaned[key] = resolved if resolved in allowed else ""
                    else:
                        cleaned[key] = visit(nested)
                return cleaned
            if isinstance(item, list):
                return [visit(nested) for nested in item]
            return item

        return visit(value)

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
                resolved_existing = Path(existing_path).resolve()
                try:
                    resolved_existing.relative_to(Path.cwd().resolve())
                except ValueError:
                    existing_path = ""
                else:
                    existing_path = str(resolved_existing)
                    artifact["fallback_path"] = existing_path
                    artifact["image_path"] = existing_path
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
            output_name = (
                f"decision_tree_rules_{run_stamp}.png"
                if tree_index == 1
                else f"decision_tree_rules_{tree_index}_{run_stamp}.png"
            )
            output_path = str(Path.cwd().resolve() / output_name)
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
        if isinstance(value, type):
            return None
        if self._has_fitted_tree_object(value):
            return value
        steps = getattr(value, "steps", None)
        if isinstance(steps, (list, tuple)) and steps:
            for _, estimator in reversed(steps):
                if self._has_fitted_tree_object(estimator):
                    return estimator
        named_steps = getattr(value, "named_steps", None)
        if named_steps and not isinstance(named_steps, property) and hasattr(named_steps, "values"):
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
        if re.search(
            r"\.loc\[[^\n]*==\s*['\"][^'\"]+['\"][^\n]*\]\s*(?:\[['\"][^'\"]+['\"]\])?\.values\s*\[\s*0\s*\]",
            code,
        ):
            issues.append(
                "do not filter for a literal category and immediately read .values[0]; rank observed groups and guard empty selections"
            )
        target_name = str(self.workflow_state.get("decision_tree_target_column", "")).strip()
        target_is_numeric = any(
            target_name in getattr(df, "columns", []) and pd.api.types.is_numeric_dtype(df[target_name])
            for df in self.workflow_state.get("csv_data", {}).values()
            if isinstance(df, pd.DataFrame)
        )
        if target_is_numeric and re.search(r"\[\s*['\"][01]['\"]\s*\]", code):
            issues.append(
                "numeric binary targets use integer 0/1 keys; do not index derived dictionaries with quoted '0' or '1'"
            )
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
        return self._missing_allowed_import_result(code)

    def _missing_allowed_import_result(self, code: str) -> dict[str, Any] | None:
        roots = self._static_import_roots(code)
        for root in sorted(roots):
            package = APPROVED_ANALYSIS_PACKAGE_INSTALLS.get(root)
            if not package:
                continue
            if importlib.util.find_spec(root) is not None:
                continue
            return {
                "execution_status": "failed",
                "error": (
                    f"Required analysis dependency '{package}' is not installed. "
                    "Install the pinned project dependencies before starting the workflow; runtime installation is disabled."
                ),
                "traceback": "",
                "missing_module": root,
                "required_package": package,
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
    output_path: OutputPath | str,
    workspace: Path | None = None,
    step_callback: StepCallback | None = None,
    ppt_mcp_deck_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = workspace or Path.cwd()
    selected_output_path = coerce_output_path(output_path)
    selected_paths = [
        path if path.is_absolute() else root / path
        for path in csv_paths
    ]
    if not selected_paths:
        raise ValueError("At least one CSV path is required for a non-interactive workflow run.")

    register_runtime_config(config)
    orchestrator = MultiAgentOrchestrator(
        config,
        step_callback=step_callback,
        workspace=root,
        output_path=selected_output_path,
    )
    if ppt_mcp_deck_spec is not None:
        if not isinstance(ppt_mcp_deck_spec, dict) or not isinstance(ppt_mcp_deck_spec.get("slides"), list):
            raise ValueError("ppt_mcp_deck_spec must be an object containing a slides array.")
        orchestrator.workflow_state["ppt_mcp_deck_spec"] = ppt_mcp_deck_spec
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


def resume_non_interactive_workflow(
    config: RuntimeConfig,
    run_directory: Path,
    *,
    step_callback: StepCallback | None = None,
) -> dict[str, Any]:
    """Resume a run using validated checkpoints already persisted in its run folder."""
    checkpoint = load_run_checkpoint(run_directory)
    run_dir = checkpoint.run_directory
    manifest = checkpoint.manifest
    outputs = checkpoint.agent_outputs
    selected_output_path = coerce_output_path(manifest.get("output_path", OutputPath.ANALYTICS_REPORT.value))

    register_runtime_config(config)
    workspace = run_dir.parent.parent if run_dir.parent.name == "runs" else run_dir.parent
    orchestrator = MultiAgentOrchestrator(
        config,
        step_callback=step_callback,
        workspace=workspace,
        create_run_directory=False,
        output_path=selected_output_path,
    )
    orchestrator.run_dir = run_dir
    orchestrator.run_id = str(manifest.get("run_id") or run_dir.name)
    orchestrator.workflow_state["run_manifest"] = manifest
    # Upgrade legacy route values (including old power_bi checkpoints) to the
    # current HTML dashboard route when resuming.
    orchestrator.workflow_state["run_manifest"]["output_path"] = orchestrator.output_path.value
    orchestrator.workflow_state["agent_outputs"] = outputs
    orchestrator.workflow_state["generated_reports"] = dict(manifest.get("reports", {}) or {})
    orchestrator.workflow_state["artifact_dependencies"] = dict(manifest.get("artifact_dependencies", {}) or {})
    orchestrator.workflow_state["saved_figures"] = [
        str(path) for path in manifest.get("figures", []) or [] if Path(str(path)).is_file()
    ]
    if checkpoint.analysis_results:
        orchestrator.workflow_state["analysis_results"] = checkpoint.analysis_results

    dataset_entries = list(manifest.get("datasets", []) or [])
    dataset_paths = checkpoint.dataset_paths
    orchestrator.workflow_state["run_manifest"]["datasets"] = []
    if not dataset_paths or not orchestrator.load_csv_paths(dataset_paths):
        orchestrator.workflow_state["run_manifest"]["datasets"] = dataset_entries
        raise RuntimeError("Run datasets are unavailable or no longer pass input validation.")
    orchestrator.workflow_state["run_manifest"]["datasets"] = dataset_entries

    inputs = manifest.get("workflow_inputs", {}) or {}
    orchestrator.set_user_data_description(str(inputs.get("user_data_description", "")))
    orchestrator.set_decision_tree_target_column(str(inputs.get("decision_tree_target_column", "")))
    orchestrator.workflow_state["status"] = "resuming"
    result = orchestrator.execute_workflow()
    result["cost_summary"] = orchestrator.openrouter_client.cost_tracker.report()
    return result


def run_terminal_workflow(
    config: RuntimeConfig,
    workspace: Path | None = None,
    *,
    output_path: OutputPath | str,
) -> int:
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
        output_path=output_path,
        workspace=root,
        step_callback=lambda number, name, status: print(
            format_step_update(number, name, status, total_steps=len(workflow_steps_for(output_path)))
        ),
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
