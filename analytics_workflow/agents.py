from __future__ import annotations

import ast
import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

from .clients import BraveSearchClient, OpenRouterClient, SharedContextStore


class BaseAgent(ABC):
    def __init__(
        self,
        name: str,
        role: str,
        expertise: str,
        openrouter_client: OpenRouterClient,
        brave_client: BraveSearchClient | None = None,
        shared_store: SharedContextStore | None = None,
    ) -> None:
        self.name = name
        self.role = role
        self.expertise = expertise
        self.openrouter_client = openrouter_client
        self.brave_client = brave_client
        self.shared_store = shared_store
        self.context: dict[str, Any] = {}
        self._logger = logging.getLogger(f"Agent.{name.replace(' ', '')}")

    def set_shared_context(self, **values: Any) -> None:
        for key, value in values.items():
            if value is None:
                continue
            self.context[key] = value

    def _system_prompt(self, extra: str = "") -> str:
        user_data_description = str(self.context.get("user_data_description", "")).strip()
        user_context = ""
        if user_data_description:
            user_context = f"\nUser-provided dataset/business description:\n{user_data_description}\n"
        return (
            f"You are a {self.role} with expertise in {self.expertise}.\n"
            "Be analytical, specific, and practical.\n"
            "Write plain text unless JSON is explicitly required.\n"
            f"{user_context}"
            f"{extra}"
        )

    def _extract_code(self, text: str) -> str:
        if "```python" in text:
            start = text.find("```python") + len("```python")
            end = text.find("```", start)
            if end != -1:
                return text[start:end].strip()
        if "```" in text:
            parts = text.split("```")
            for block in parts:
                candidate = block.strip()
                if candidate and self._is_compilable_python(candidate):
                    return candidate
        lines = text.splitlines()
        starters = (
            "import ",
            "from ",
            "try:",
            "for ",
            "while ",
            "if ",
            "def ",
            "class ",
            "analysis_summary",
            "business_findings",
            "figure_captions",
            "warnings.",
            "plt.",
            "sns.",
        )
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(starters):
                candidate = "\n".join(lines[index:]).strip()
                if candidate:
                    return candidate
        return text.strip()

    def _is_compilable_python(self, text: str) -> bool:
        candidate = text.strip()
        if not candidate:
            return False
        try:
            ast.parse(candidate)
        except SyntaxError:
            return False
        return True

    @abstractmethod
    def execute(self, *args: Any, **kwargs: Any) -> Any:
        pass


class DataUnderstanderAgent(BaseAgent):
    def execute(self, csv_data: dict[str, pd.DataFrame]) -> dict[str, Any]:
        summary = {}
        for name, df in csv_data.items():
            summary[name] = {
                "shape": list(df.shape),
                "columns": list(df.columns),
                "dtypes": {str(k): str(v) for k, v in df.dtypes.to_dict().items()},
                "sample_data": df.head(3).to_dict("records"),
            }
        schema = {
            "overall_quality_score": "integer 0-100",
            "datasets": {"<dataset>": {"quality_summary": "string", "recommended_analyses": ["string"]}},
            "executive_summary": "string",
        }
        return self.openrouter_client.chat_completion_json(
            self._system_prompt("Return concise JSON."),
            f"Analyze this dataset summary:\n{json.dumps(summary, indent=2, default=str)}",
            schema,
        )


class MarketResearcherAgent(BaseAgent):
    def execute(self, data_context: dict[str, Any]) -> dict[str, Any]:
        searches = []
        if self.brave_client:
            for query in [
                "stock market trends 2026",
                "telecom sector outlook 2026",
                "business intelligence best practices 2026",
            ]:
                searches.extend(self.brave_client.search(query))
        schema = {
            "industry_overview": "string",
            "market_findings": [{"claim": "string", "source_index": "integer"}],
            "key_trends": ["string"],
            "opportunities": ["string"],
            "sources_cited": [{"index": "integer", "title": "string", "url": "string", "relevance": "string"}],
        }
        return self.openrouter_client.chat_completion_json(
            self._system_prompt("Use the provided sources and tie each claim to a numbered citation."),
            f"Use this data context and sources to produce market research:\n"
            f"DATA:\n{json.dumps(data_context, indent=2, default=str)[:2500]}\n"
            f"SOURCES:\n{json.dumps(searches, indent=2)[:2500]}\n"
            "Return source indexes so each important market claim can be shown with [1], [2], etc.",
            schema,
        )


class PlannerAgent(BaseAgent):
    def execute(self, data_insights: dict[str, Any], market_insights: dict[str, Any]) -> dict[str, Any]:
        schema = {
            "objectives": ["string"],
            "hypotheses": ["string"],
            "statistical_methods": ["string"],
            "visualization_plan": ["string"],
            "success_metrics": ["string"],
        }
        return self.openrouter_client.chat_completion_json(
            self._system_prompt("Build a decision-oriented plan."),
            f"Create an analysis plan from:\nDATA:\n{json.dumps(data_insights, indent=2, default=str)[:1800]}\n"
            f"MARKET:\n{json.dumps(market_insights, indent=2, default=str)[:1800]}",
            schema,
        )


class DataScientistCoderAgent(BaseAgent):
    def execute(self, analysis_plan: dict[str, Any], csv_data: dict[str, pd.DataFrame], iteration: int = 1) -> str:
        review_feedback = self.context.get("review_feedback", "")
        dataset_runtime_context = self._dataset_runtime_context(csv_data)
        prompt = f"""Generate ONLY executable Python code.

ANALYSIS PLAN:
{json.dumps(analysis_plan, indent=2, default=str)[:2200]}

DATASETS:
{json.dumps({name: {"shape": list(df.shape), "columns": list(df.columns)} for name, df in csv_data.items()}, indent=2)}

RUNTIME DATAFRAME VARIABLES:
{dataset_runtime_context}

REVIEW FEEDBACK:
{review_feedback[:1200]}

Rules:
- Start with imports
- Produce business-relevant analysis, not generic EDA
- Save charts as figure_1.png, figure_2.png, etc.
- Create charts that explain trends, comparisons, drivers, or risks
- Give each chart a clear title, axis labels, and legend when needed
- Build an `analysis_summary` dict with business-facing findings and numeric evidence
- Build a `figure_captions` dict mapping figure file names to one-sentence business interpretations
- Build a `business_findings` list of concise insight statements tied to the data
- Use try-except around major blocks
- Use pandas/numpy/matplotlib/seaborn
- No markdown, no explanation"""
        corrective_note = ""
        last_candidate = ""
        for _attempt in range(3):
            raw = self.openrouter_client.chat_completion(
                self._system_prompt("Return Python code only."),
                f"{prompt}\n\n{corrective_note}".strip(),
            )
            candidate = self._extract_code(raw)
            if self._is_compilable_python(candidate):
                return candidate
            last_candidate = candidate or raw.strip()
            corrective_note = (
                "Previous response was not valid Python. "
                "Return executable Python only, starting with imports, with no JSON and no prose."
            )
        return last_candidate

    def _dataset_runtime_context(self, csv_data: dict[str, pd.DataFrame]) -> str:
        lines = []
        dataset_names = list(csv_data.keys())
        if len(dataset_names) == 1:
            lines.append("Single-dataset shortcut: `df` is available for the only dataset.")

        for name in dataset_names:
            clean = re.sub(r"[^a-zA-Z0-9_]", "_", name.replace(".csv", ""))
            lines.append(
                f"- {name}: df_{clean} (main dataframe), "
                f"df_{clean}_numeric (numeric columns), "
                f"df_{clean}_categorical (categorical columns)"
            )
        return "\n".join(lines)


class DataScientistReviewerAgent(BaseAgent):
    def execute(
        self,
        code: str,
        analysis_plan: dict[str, Any],
        iteration: int,
        execution: dict[str, Any] | None = None,
        artifact_issues: list[str] | None = None,
    ) -> dict[str, Any]:
        schema = {
            "quality_score": "integer 1-10",
            "decision": "APPROVE or REVISE or REJECT",
            "critical_issues": ["string"],
            "improvements": ["string"],
            "summary": "string",
        }
        return self.openrouter_client.chat_completion_json(
            self._system_prompt("Review analytical quality, business fit, and chart usefulness."),
            f"Review this analysis code against the plan.\nITERATION: {iteration}\nPLAN:\n"
            f"{json.dumps(analysis_plan, indent=2, default=str)[:1800]}\nCODE:\n{code[:4500]}\n"
            f"EXECUTION RESULT:\n{json.dumps(execution or {}, indent=2, default=str)[:1800]}\n"
            f"ARTIFACT ISSUES:\n{json.dumps(artifact_issues or [], indent=2, default=str)}\n"
            "Reject or request revision if the analysis lacks meaningful visuals, numeric evidence, or business-ready findings.",
            schema,
        )


class BusinessInsightsTranslatorAgent(BaseAgent):
    def execute(
        self,
        analysis_results: dict[str, Any],
        data_context: dict[str, Any],
        market_context: dict[str, Any],
    ) -> dict[str, Any]:
        schema = {
            "executive_summary": "string",
            "key_findings": [{"finding": "string", "business_implication": "string", "priority": "High/Medium/Low"}],
            "business_narrative": "string",
            "risks": ["string"],
            "opportunities": ["string"],
            "immediate_actions": ["string"],
        }
        return self.openrouter_client.chat_completion_json(
            self._system_prompt("Translate technical analysis into business meaning and decision context."),
            f"Translate these results into business insights.\nDATA:\n{json.dumps(data_context, indent=2, default=str)[:1800]}\n"
            f"MARKET:\n{json.dumps(market_context, indent=2, default=str)[:1600]}\n"
            f"RESULTS:\n{json.dumps(analysis_results, indent=2, default=str)[:2200]}\n"
            "Focus on why the analysis matters to the business, what the evidence suggests, and what managers should do next.",
            schema,
        )


class DecisionMakerAgent(BaseAgent):
    def execute(self, all_outputs: dict[str, Any], analysis_results: dict[str, Any], business_insights: dict[str, Any]) -> dict[str, Any]:
        schema = {
            "title": "string",
            "executive_summary": "string",
            "decision_context": "string",
            "recommendations": [{"rank": "integer", "action": "string", "rationale": "string", "evidence": "string", "timeline": "string", "impact": "High/Medium/Low"}],
            "final_recommendation": "string",
            "conclusion": "string",
        }
        return self.openrouter_client.chat_completion_json(
            self._system_prompt("Create a decision-ready executive report with strong business framing."),
            f"Compile a decision report from:\nOUTPUTS:\n{json.dumps(all_outputs, indent=2, default=str)[:2500]}\n"
            f"RESULTS:\n{json.dumps(analysis_results, indent=2, default=str)[:1500]}\n"
            f"BUSINESS:\n{json.dumps(business_insights, indent=2, default=str)[:1500]}\n"
            "Recommendations must say what to do, why to do it, and what evidence supports it.",
            schema,
        )


class PresentationArchitectAgent(BaseAgent):
    def execute(self, workflow_state: dict[str, Any]) -> dict[str, Any]:
        schema = {
            "presentation_title": "string",
            "presentation_subtitle": "string",
            "slides": [{"slide_number": "integer", "title": "string", "main_message": "string", "details": ["string"], "visual_element": "string"}],
        }
        return self.openrouter_client.chat_completion_json(
            self._system_prompt("Create a concise executive deck structure in a consulting style."),
            f"Design a slide deck from this workflow state:\n{json.dumps(workflow_state.get('agent_outputs', {}), indent=2, default=str)[:2800]}\n"
            f"FIGURES:\n{json.dumps(workflow_state.get('saved_figures', []), indent=2)}\n"
            "Use a short MBB-style flow: context, objective, dataset, market, analysis, findings, business meaning, options, recommendation.",
            schema,
        )
