from __future__ import annotations

import ast
from functools import lru_cache
import logging
from pathlib import Path
import re
from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

from .clients import BraveSearchClient, OpenRouterClient, SharedContextStore
from .serialization import json_dumps_safe


@lru_cache(maxsize=None)
def _load_repo_skill_text(skill_name: str) -> str:
    skill_path = Path(__file__).resolve().parents[1] / ".codex" / "skills" / skill_name / "SKILL.md"
    if not skill_path.exists():
        return ""
    try:
        return skill_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


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
        primary = ""
        if "```python" in text:
            start = text.find("```python") + len("```python")
            end = text.find("```", start)
            if end != -1:
                primary = text[start:end].strip()
        if not primary and "```" in text:
            parts = text.split("```")
            for block in parts:
                candidate = block.strip()
                if candidate and self._looks_like_analysis_script(candidate):
                    return candidate
            for block in parts:
                candidate = block.strip()
                if candidate:
                    primary = candidate
                    break
        if not primary:
            primary = self._extract_python_window(text)
        primary = primary or text.strip()
        primary = self._sanitize_candidate_code(primary)
        if self._looks_like_analysis_script(primary):
            return primary
        repaired = self._repair_python_code(primary)
        if self._looks_like_analysis_script(repaired):
            return repaired
        return ""

    def _sanitize_candidate_code(self, text: str) -> str:
        lines = text.strip().splitlines()
        if not lines:
            return ""

        cleaned = [line for line in lines if not line.strip().startswith("```")]
        while cleaned and not cleaned[0].strip():
            cleaned = cleaned[1:]
        if cleaned and cleaned[0].strip().lower() in {"python", "py"}:
            cleaned = cleaned[1:]
        return "\n".join(cleaned).strip()

    def _is_compilable_python(self, text: str) -> bool:
        candidate = text.strip()
        if not candidate:
            return False
        try:
            ast.parse(candidate)
        except SyntaxError:
            return False
        return True

    def _python_syntax_error(self, text: str) -> SyntaxError | None:
        candidate = text.strip()
        if not candidate:
            return SyntaxError("empty code")
        try:
            ast.parse(candidate)
        except SyntaxError as exc:
            return exc
        return None

    def _looks_like_analysis_script(self, text: str) -> bool:
        candidate = text.strip()
        if not self._is_compilable_python(candidate):
            return False
        assigned_names = self._assigned_names(candidate)
        required_markers = {"analysis_summary", "figure_captions"}
        has_required_markers = required_markers.issubset(assigned_names)
        has_python_structure = (
            "import " in candidate
            or "from " in candidate
            or "plt.savefig" in candidate
            or "sns." in candidate
        )
        return has_required_markers and has_python_structure

    def _assigned_names(self, text: str) -> set[str]:
        try:
            tree = ast.parse(text.strip())
        except SyntaxError:
            return set()
        assigned: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                assigned.add(node.target.id)
        return assigned

    def _extract_python_window(self, text: str) -> str:
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
                "figure_captions",
            "warnings.",
            "plt.",
            "sns.",
        )
        started = False
        collected: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not started:
                if stripped.startswith(starters):
                    started = True
                    collected.append(line)
                continue
            if not stripped:
                collected.append(line)
                continue
            if (
                line.startswith((" ", "\t"))
                or stripped.startswith(starters)
                or "=" in stripped
                or stripped.startswith(("#", "except", "elif", "else:", "return", "pass", "break", "continue", "with "))
            ):
                collected.append(line)
                continue
            # Stop when we hit obvious prose after code has started.
            if re.match(r"^[A-Z][a-z]+(?:\s+[a-zA-Z]+){2,}", stripped):
                break
            collected.append(line)
        return "\n".join(collected).strip()

    def _repair_python_code(self, text: str) -> str:
        candidate = text.strip()
        if not candidate or self._is_compilable_python(candidate):
            return candidate

        pairs = {")": "(", "]": "[", "}": "{"}
        openers = {v: k for k, v in pairs.items()}
        stack: list[str] = []
        in_string: str | None = None
        i = 0
        while i < len(candidate):
            ch = candidate[i]
            if in_string:
                if ch == "\\":
                    i += 2
                    continue
                if candidate.startswith(in_string, i):
                    i += len(in_string)
                    in_string = None
                    continue
                i += 1
                continue
            if ch == "#":
                newline = candidate.find("\n", i)
                if newline == -1:
                    break
                i = newline
                continue
            for triple in ('"""', "'''"):
                if candidate.startswith(triple, i):
                    in_string = triple
                    i += 3
                    break
            else:
                if ch in ('"', "'"):
                    in_string = ch
                    i += 1
                    continue
                if ch in openers:
                    stack.append(ch)
                elif ch in pairs:
                    if stack and stack[-1] == pairs[ch]:
                        stack.pop()
                i += 1

        if stack and not in_string:
            closer_suffix = "".join(openers[opener] for opener in reversed(stack))
            patched = f"{candidate}\n{closer_suffix}"
            if self._is_compilable_python(patched):
                return patched

        lines = candidate.splitlines()
        for cut in range(len(lines) - 1, 0, -1):
            prefix = "\n".join(lines[:cut]).rstrip()
            if not prefix:
                break
            if self._is_compilable_python(prefix):
                return prefix
        return candidate

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
            f"Analyze this dataset summary:\n{json_dumps_safe(summary, indent=2)}",
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
            f"DATA:\n{json_dumps_safe(data_context, indent=2)[:2500]}\n"
            f"SOURCES:\n{json_dumps_safe(searches, indent=2)[:2500]}\n"
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
            f"Create an analysis plan from:\nDATA:\n{json_dumps_safe(data_insights, indent=2)[:1800]}\n"
            f"MARKET:\n{json_dumps_safe(market_insights, indent=2)[:1800]}",
            schema,
        )


class DataScientistCoderAgent(BaseAgent):
    def execute(self, analysis_plan: dict[str, Any], csv_data: dict[str, pd.DataFrame], iteration: int = 1) -> str:
        review_feedback = self.context.get("review_feedback", "")
        dataset_runtime_context = self._dataset_runtime_context(csv_data)
        analysis_skill = _load_repo_skill_text("generate-analysis-code")
        prompt = f"""Generate ONLY executable Python code.

ANALYSIS PLAN:
{json_dumps_safe(analysis_plan, indent=2)[:2200]}

DATASETS:
{json_dumps_safe({name: {"shape": list(df.shape), "columns": list(df.columns)} for name, df in csv_data.items()}, indent=2)}

RUNTIME DATAFRAME VARIABLES:
{dataset_runtime_context}

ANALYSIS SKILL:
{analysis_skill[:3200]}

REVIEW FEEDBACK:
{review_feedback[:1200]}

Rules:
- Start with imports
- Produce business-relevant analysis, not generic EDA
- Save charts as figure_1.png, figure_2.png, etc.
- Create charts that explain trends, comparisons, drivers, or risks
- Give each chart a clear title, axis labels, and legend when needed
- Build an `analysis_summary` dict with business-facing findings and numeric evidence
- Build a `business_findings` list with concise, evidence-backed bullet points for reporting
- Build a `figure_captions` dict mapping figure file names to one-sentence business interpretations
- Build an `analysis_summary` dict with technical findings and numeric evidence that a downstream business translator can interpret
- Use try-except around major blocks
- You may use any installed Python package that clearly helps the analysis.
- Prefer common analytics libraries such as pandas, numpy, matplotlib, seaborn, scipy, and statsmodels when they are sufficient.
- Avoid niche dependencies unless they are genuinely needed for the requested analysis.
- If review feedback says a package was missing at runtime, either add the missing import if you forgot it, or add a small bootstrap block that installs the package with `sys.executable -m pip install <package>` before importing it.
- No markdown, no explanation"""
        corrective_note = ""
        last_candidate = ""
        for _attempt in range(3):
            raw = self.openrouter_client.chat_completion(
                self._system_prompt("Return Python code only."),
                f"{prompt}\n\n{corrective_note}".strip(),
            )
            candidate = self._extract_code(raw)
            if self._looks_like_analysis_script(candidate):
                return candidate
            last_candidate = candidate or raw.strip()
            syntax_error = self._python_syntax_error(last_candidate)
            corrective_note = self._format_syntax_corrective_note(last_candidate, syntax_error)
        raise RuntimeError("Model did not return a valid analysis script after 3 attempts.")

    def _format_syntax_corrective_note(self, candidate: str, error: SyntaxError | None) -> str:
        if error is None:
            return (
                "Previous response was not valid Python. "
                "Return executable Python only, starting with imports, with no JSON and no prose."
            )
        lines = candidate.splitlines()
        lineno = getattr(error, "lineno", None) or 0
        offending = lines[lineno - 1].rstrip() if 1 <= lineno <= len(lines) else ""
        location = f" at line {lineno}" if lineno else ""
        offending_block = f"\nOffending line: {offending}" if offending else ""
        return (
            "Previous response did not parse as Python.\n"
            f"SyntaxError: {error.msg}{location}.{offending_block}\n"
            "Return a COMPLETE, executable Python script. "
            "Close every opening parenthesis, bracket, brace, and string. "
            "Do not truncate the script mid-expression. "
            "Start with imports. Include analysis_summary, business_findings, and figure_captions assignments. "
            "No markdown fences, no prose, no JSON."
        )

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
        analysis_skill = _load_repo_skill_text("generate-analysis-code")
        return self.openrouter_client.chat_completion_json(
            self._system_prompt("Review analytical quality, business fit, and chart usefulness."),
            f"Review this analysis code against the plan.\nANALYSIS SKILL:\n{analysis_skill[:2600]}\n"
            f"ITERATION: {iteration}\nPLAN:\n"
            f"{json_dumps_safe(analysis_plan, indent=2)[:1800]}\nCODE:\n{code[:4500]}\n"
            f"EXECUTION RESULT:\n{json_dumps_safe(execution or {}, indent=2)[:1800]}\n"
            f"ARTIFACT ISSUES:\n{json_dumps_safe(artifact_issues or [], indent=2)}\n"
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
            f"Translate these results into business insights.\nDATA:\n{json_dumps_safe(data_context, indent=2)[:1800]}\n"
            f"MARKET:\n{json_dumps_safe(market_context, indent=2)[:1600]}\n"
            f"RESULTS:\n{json_dumps_safe(analysis_results, indent=2)[:2200]}\n"
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
            f"Compile a decision report from:\nOUTPUTS:\n{json_dumps_safe(all_outputs, indent=2)[:2500]}\n"
            f"RESULTS:\n{json_dumps_safe(analysis_results, indent=2)[:1500]}\n"
            f"BUSINESS:\n{json_dumps_safe(business_insights, indent=2)[:1500]}\n"
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
            f"Design a slide deck from this workflow state:\n{json_dumps_safe(workflow_state.get('agent_outputs', {}), indent=2)[:2800]}\n"
            f"FIGURES:\n{json_dumps_safe(workflow_state.get('saved_figures', []), indent=2)}\n"
            "Use a short MBB-style flow: context, objective, dataset, market, analysis, findings, business meaning, options, recommendation.",
            schema,
        )
