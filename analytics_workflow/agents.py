from __future__ import annotations

import ast
from functools import lru_cache
import logging
from pathlib import Path
import re
import warnings
from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

from .clients import BraveSearchClient, OpenRouterClient, SharedContextStore
from .project_skills import load_project_skill
from .serialization import json_dumps_safe


@lru_cache(maxsize=None)
def _load_repo_skill_text(skill_name: str) -> str:
    project_skill_aliases = {
        "generate-analysis-code": "code_generation",
        "review-analysis-code": "code_review",
        "debug-analysis-code": "code_review",
        "generate-pdf-report": "report_generation",
        "generate-slide-deck": "slide_generation",
    }
    project_skill_name = project_skill_aliases.get(skill_name)
    if project_skill_name:
        project_skill = load_project_skill(project_skill_name)
        if project_skill:
            return project_skill

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
        decision_tree_target = str(self.context.get("decision_tree_target_column", "")).strip()
        workflow_objective = self.context.get("workflow_objective", {})
        user_context = ""
        if user_data_description:
            user_context = (
                "\nUser-provided dataset/business description:\n"
                f"{user_data_description}\n"
                "Use this as an explicit parameter for goals, KPIs, analysis selection, "
                "reporting language, and recommendation framing. If the data does not "
                "support part of it, state that limitation.\n"
            )
        target_context = ""
        if decision_tree_target:
            target_context = (
                "\nDecision tree modeling request:\n"
                f"Target column: {decision_tree_target}\n"
                "If this target is present in the loaded data, perform one interpretable decision tree model after EDA. "
                "Choose classification or regression from the target data type, report model metrics, and expose the tree rules for reports and slides.\n"
            )
        objective_context = ""
        if isinstance(workflow_objective, dict) and workflow_objective:
            objective_context = (
                "\nWorkflow objective contract:\n"
                f"{json_dumps_safe(workflow_objective, indent=2)[:1200]}\n"
            )
        return (
            f"You are a {self.role} with expertise in {self.expertise}.\n"
            "Be analytical, specific, and practical.\n"
            "Write plain text unless JSON is explicitly required.\n"
            f"{user_context}"
            f"{target_context}"
            f"{objective_context}"
            f"{extra}"
        )

    def _project_skill_prompt(self, skill_name: str, limit: int = 1800) -> str:
        skill = load_project_skill(skill_name)
        if not skill:
            return ""
        label = skill_name.replace("_", " ").upper()
        return f"\nPROJECT {label} SKILL:\n{skill[:limit]}\n"

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
        required_markers = {"analysis_summary", "business_findings", "figure_captions", "analysis_artifacts"}
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
            column_profiles = self._column_profiles(df)
            summary[name] = {
                "shape": list(df.shape),
                "columns": list(df.columns),
                "dtypes": {str(k): str(v) for k, v in df.dtypes.to_dict().items()},
                "missing_values": {str(k): int(v) for k, v in df.isna().sum().to_dict().items()},
                "missing_pct": {
                    str(k): round(float(v), 2)
                    for k, v in (df.isna().mean() * 100).to_dict().items()
                },
                "duplicate_rows": int(df.duplicated().sum()),
                "column_profiles": column_profiles,
                "candidate_analysis_families": self._candidate_analysis_families(column_profiles, len(df)),
                "sample_data": df.head(3).to_dict("records"),
            }
        schema = {
            "overall_quality_score": "integer 0-100",
            "datasets": {
                "<dataset>": {
                    "quality_summary": "string",
                    "cleaning_priorities": ["string"],
                    "type_notes": ["string"],
                    "outlier_notes": ["string"],
                    "recommended_analyses": ["string"],
                    "analyses_to_avoid": ["string"],
                }
            },
            "executive_summary": "string",
        }
        return self.openrouter_client.chat_completion_json(
            self._system_prompt(
                "Return concise JSON. Use the deterministic Python profile to reason about data types, "
                "missingness, outliers, cleaning needs, and which analysis families the data can support. "
                "Use the user's description as the objective lens for data readiness and recommended analysis."
                f"{self._project_skill_prompt('data_profiling')}"
            ),
            f"Analyze this dataset summary:\n{json_dumps_safe(summary, indent=2)}",
            schema,
        )

    def _column_profiles(self, df: pd.DataFrame) -> dict[str, dict[str, Any]]:
        profiles: dict[str, dict[str, Any]] = {}
        row_count = max(len(df), 1)
        for column in df.columns:
            series = df[column]
            non_null = series.dropna()
            role = self._infer_column_role(column, series)
            profile: dict[str, Any] = {
                "dtype": str(series.dtype),
                "role": role,
                "missing_pct": round(float(series.isna().mean() * 100), 2),
                "unique_count": int(series.nunique(dropna=True)),
                "unique_pct": round(float(series.nunique(dropna=True) / row_count * 100), 2),
            }
            if role in {"numeric-continuous", "numeric-discrete"}:
                numeric = pd.to_numeric(series, errors="coerce").dropna()
                if not numeric.empty:
                    q1 = float(numeric.quantile(0.25))
                    q3 = float(numeric.quantile(0.75))
                    iqr = q3 - q1
                    lower = q1 - 1.5 * iqr
                    upper = q3 + 1.5 * iqr
                    outliers = numeric[(numeric < lower) | (numeric > upper)]
                    profile.update(
                        {
                            "min": float(numeric.min()),
                            "median": float(numeric.median()),
                            "mean": float(numeric.mean()),
                            "max": float(numeric.max()),
                            "outlier_count_iqr": int(outliers.count()) if iqr else 0,
                            "outlier_pct_iqr": round(float(outliers.count() / row_count * 100), 2) if iqr else 0.0,
                        }
                    )
            elif role in {"nominal", "ordinal", "binary"}:
                top_values = non_null.astype(str).str.strip().value_counts().head(5)
                profile["top_values"] = top_values.to_dict()
            profiles[str(column)] = profile
        return profiles

    def _infer_column_role(self, column: str, series: pd.Series) -> str:
        name = column.lower()
        non_null = series.dropna()
        unique_count = int(non_null.nunique())
        row_count = max(len(series), 1)

        if unique_count <= 2 and unique_count > 0:
            return "binary"
        if self._is_identifier_name(name):
            return "identifier"
        if pd.api.types.is_datetime64_any_dtype(series):
            return "datetime"
        if pd.api.types.is_numeric_dtype(series):
            if unique_count <= min(20, max(5, row_count * 0.05)):
                return "numeric-discrete"
            return "numeric-continuous"

        sample = non_null.astype(str).str.strip()
        if not sample.empty:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                datetime_parse_rate = pd.to_datetime(sample.head(200), errors="coerce").notna().mean()
            numeric_parse_rate = pd.to_numeric(sample.head(200), errors="coerce").notna().mean()
            avg_length = sample.head(200).str.len().mean()
            if datetime_parse_rate >= 0.8:
                return "datetime"
            if numeric_parse_rate >= 0.8:
                return "numeric-continuous"
            if avg_length and avg_length > 80:
                return "free-text"

        ordinal_terms = {"low", "medium", "high", "very high", "poor", "fair", "good", "excellent"}
        observed = {value.lower() for value in sample.head(100).unique()}
        if observed and observed.issubset(ordinal_terms):
            return "ordinal"
        return "nominal"

    def _is_identifier_name(self, name: str) -> bool:
        return (
            name == "id"
            or name.endswith(("_id", " id", "_key", " key"))
            or "uuid" in name
            or bool(re.search(r"(?:^|[_\s])(invoice|order|account|record)[_\s]?(?:id|key|no|number)$", name))
        )

    def _candidate_analysis_families(self, profiles: dict[str, dict[str, Any]], row_count: int) -> list[str]:
        roles = [profile.get("role", "") for profile in profiles.values()]
        has_datetime = "datetime" in roles
        numeric_count = sum(role in {"numeric-continuous", "numeric-discrete"} for role in roles)
        categorical_count = sum(role in {"nominal", "ordinal", "binary"} for role in roles)
        families = ["EDA"]
        if has_datetime and numeric_count:
            families.append("Trend / time series")
        if numeric_count >= 2:
            families.append("Correlation")
        if categorical_count >= 2 or (categorical_count >= 1 and numeric_count >= 1):
            families.append("Association / segment comparison")
        if row_count >= 30 and numeric_count >= 2:
            families.append("Clustering")
        if numeric_count >= 1:
            families.append("Outlier / anomaly analysis")
        return families


class MarketResearcherAgent(BaseAgent):
    def execute(self, data_context: dict[str, Any]) -> dict[str, Any]:
        searches = []
        queries = self._generate_queries(data_context)
        if self.brave_client:
            for query in queries:
                searches.extend(self.brave_client.search(query))
        schema = {
            "industry_overview": "string",
            "market_findings": [{"claim": "string", "source_index": "integer"}],
            "key_trends": ["string"],
            "opportunities": ["string"],
            "sources_cited": [{"index": "integer", "title": "string", "url": "string", "relevance": "string"}],
            "search_queries": ["string"],
        }
        return self.openrouter_client.chat_completion_json(
            self._system_prompt(
                "Use the provided sources and tie each claim to a numbered citation. "
                "Prioritize market context that helps answer the user's described business problem."
                f"{self._project_skill_prompt('market_research')}"
            ),
            f"Use this data context and sources to produce market research:\n"
            f"SEARCH QUERIES USED:\n{json_dumps_safe(queries, indent=2)}\n"
            f"DATA:\n{json_dumps_safe(data_context, indent=2)[:2500]}\n"
            f"SOURCES:\n{json_dumps_safe(searches, indent=2)[:2500]}\n"
            "Return source indexes so each important market claim can be shown with [1], [2], etc. "
            "Also return the exact search_queries list.",
            schema,
        )

    def _generate_queries(self, data_context: dict[str, Any]) -> list[str]:
        objective = self.context.get("workflow_objective", {}) if isinstance(self.context.get("workflow_objective", {}), dict) else {}
        focus_terms = [str(term) for term in objective.get("focus_terms", [])[:5]]
        kpi_hints = [str(term) for term in objective.get("kpi_hints", [])[:4]]
        columns: list[str] = []
        datasets = data_context.get("datasets", {}) if isinstance(data_context, dict) else {}
        if isinstance(datasets, dict):
            for dataset in datasets.values():
                if isinstance(dataset, dict):
                    columns.extend(str(column) for column in dataset.get("columns", [])[:8])
        domain_terms = focus_terms or [term for column in columns for term in re.findall(r"[A-Za-z]{3,}", column.lower())[:2]]
        domain = " ".join(dict.fromkeys(domain_terms[:4])) or "business analytics"
        metric = " ".join(dict.fromkeys(kpi_hints[:2])) or "performance"
        return [
            f"{domain} {metric} market trends 2026",
            f"{domain} industry benchmarks {metric} 2026",
            f"{domain} business risks opportunities 2026",
        ]


class PlannerAgent(BaseAgent):
    def execute(self, data_insights: dict[str, Any], market_insights: dict[str, Any]) -> dict[str, Any]:
        schema = {
            "objectives": ["string"],
            "hypotheses": ["string"],
            "data_cleaning_plan": ["string"],
            "column_role_strategy": ["string"],
            "statistical_methods": ["string"],
            "analysis_families": ["EDA, Trend/time series, Correlation, Association, Clustering, Causal/driver, Prediction, Anomaly"],
            "visualization_plan": ["string"],
            "analysis_rules": ["string"],
            "methods_to_avoid": ["string"],
            "success_metrics": ["string"],
        }
        return self.openrouter_client.chat_completion_json(
            self._system_prompt(
                "Build a decision-oriented plan. Choose analysis families based on the data profile, "
                "column roles, missingness, outliers, and the user's goal. Do not recommend causal, "
                "clustering, predictive, correlation, or association methods unless the data can support them."
                f"{self._project_skill_prompt('analysis_planning')}"
            ),
            f"Create an analysis plan that uses the user's description as the objective lens.\n"
            f"DATA:\n{json_dumps_safe(data_insights, indent=2)[:1800]}\n"
            f"MARKET:\n{json_dumps_safe(market_insights, indent=2)[:1800]}",
            schema,
        )


class DataScientistCoderAgent(BaseAgent):
    _GENERATION_ATTEMPTS_PER_ITERATION = 2

    def execute(self, analysis_plan: dict[str, Any], csv_data: dict[str, pd.DataFrame], iteration: int = 1) -> str:
        review_feedback = self.context.get("review_feedback", "")
        code_loop_context_log = self.context.get("code_loop_context_log", "")
        progress_callback = self.context.get("progress_callback")
        user_description = str(self.context.get("user_data_description", "")).strip()
        decision_tree_target = str(self.context.get("decision_tree_target_column", "")).strip()
        dataset_runtime_context = self._dataset_runtime_context(csv_data)
        dataset_generation_context = self._dataset_generation_context(csv_data)
        visual_plan = self._suited_visual_plan(csv_data, analysis_plan)
        data_understanding = self.context.get("data_understanding", {})
        analysis_skill = _load_repo_skill_text("generate-analysis-code")
        workflow_objective = self.context.get("workflow_objective", {})
        prompt = f"""Generate ONLY executable Python code.

USER DESCRIPTION PARAMETER:
{user_description or "No user description provided. Infer the most useful decision context from the data profile."}

WORKFLOW OBJECTIVE CONTRACT:
{json_dumps_safe(workflow_objective, indent=2)[:1400]}

DECISION TREE MODEL REQUEST:
{self._decision_tree_prompt_context(decision_tree_target, csv_data)}

ANALYSIS PLAN:
{json_dumps_safe(analysis_plan, indent=2)[:2200]}

DATASETS:
{json_dumps_safe({name: {"shape": list(df.shape), "columns": list(df.columns)} for name, df in csv_data.items()}, indent=2)}

DATA PROFILE FOR CODE GENERATION:
{json_dumps_safe(dataset_generation_context, indent=2)[:7000]}

DATA UNDERSTANDING OUTPUT:
{json_dumps_safe(data_understanding, indent=2)[:3000]}

SUITED VISUAL PLAN FROM DATA UNDERSTANDING:
{json_dumps_safe(visual_plan, indent=2)[:3000]}

RUNTIME DATAFRAME VARIABLES:
{dataset_runtime_context}

ANALYSIS SKILL:
{analysis_skill[:3200]}

TEMPORARY CODE LOOP CONTEXT LOG:
{str(code_loop_context_log)[:2200] or "No previous code-loop failures in this run."}

REVIEW FEEDBACK:
{review_feedback[:1200]}

Rules:
- Start with imports
- Use the user description as the analysis objective. Let it influence KPI selection, segment choices, chart titles, and business findings.
- If the dataset cannot answer part of the user's description, record that limitation in `analysis_summary`.
- Follow this process in code: classify column roles, clean/convert types, profile missingness and duplicates, detect outliers, choose supported analysis families, then create visuals and findings.
- Use the provided dtype, role hint, cardinality, missingness, numeric range, and safe sample values before writing dataset-specific logic. Do not invent columns or assume targets that are absent from the profile.
- Do not invent category values. Build category lists from observed values, value_counts, or grouped rows; if a named category is absent, record that limitation instead of indexing it directly.
- Use exact observed column names from the profile and df.columns. If a tempting column such as a satisfaction score or target is absent, choose an observed substitute and record the limitation.
- Use the SUITED VISUAL PLAN as the first choice for EDA charts. Replace a suggested visual only if the referenced columns are missing after cleaning, and explain the replacement in `analysis_summary`.
- Do not create randomized or decorative charts. Every saved figure must map to a role-supported pairing such as time+numeric, category+target rate, category+numeric, numeric distribution, or numeric correlation.
- Prefer focused EDA charts that answer one question at a time. Do not mix unrelated categorical dimensions in one chart unless it is explicitly a labeled cross-field scan.
- For credit-risk or lending datasets, prefer binned/ranked visuals around loan grade, debt burden (`loan_percent_income`), income bands, interest-rate buckets, home ownership, and prior-default flags. Avoid standalone age or scatter charts unless they directly answer the user objective.
- Figure captions must mention only fields visible in that figure. Keep EDA driver language separate from model-rule language and business hypotheses.
- For long-form metric/value summary tables, use the observed metric labels as data values. Do not invent sanitized metric keys; build guarded lookups from the metric column and check a label exists before reading it.
- Keep numeric dataframe columns numeric. If a numeric metric needs a display label such as a month name, keep that label in a separate variable or string column instead of assigning it into the numeric value column.
- Produce business-relevant analysis, not generic EDA
- Perform EDA first, then add only the supported analysis families from the plan: trend/time series, correlation, association, clustering, causal/driver, prediction, or anomaly analysis.
- If DECISION TREE MODEL REQUEST names a valid target column, perform one interpretable decision tree model after EDA and the core analysis. If no target column is provided or the target is absent, skip decision tree modeling and record that it was skipped in `analysis_summary`.
- For a decision tree target, choose `DecisionTreeClassifier` for binary, nominal, ordinal, or low-cardinality discrete targets; choose `DecisionTreeRegressor` for continuous numeric targets. Use train/test split, simple imputation, one-hot encoding for categorical features, and a bounded tree such as max_depth=3 or 4 with random_state=42.
- Exclude the target itself, identifier columns, free-text columns, leakage-prone duplicate outcome columns, and near-constant fields from decision tree features. Use only columns present in the data profile.
- For classification, report training accuracy, test accuracy, and baseline accuracy in `analysis_summary`. For regression, report training R2, test R2, train/test MAE, and include a `decision_tree_accuracy_note` explaining that R2 is the regression score rather than classification accuracy.
- If classification test accuracy is lower than baseline accuracy, call the tree an explanatory rule model only. Do not describe it as high accuracy, predictive lift, or production-ready.
- Export readable tree rules from the fitted model, not handwritten rules. Prefer `build_sklearn_tree_artifact(fitted_tree_pipeline_or_model, feature_names=None, target=..., model_type=..., train_score=..., test_score=..., baseline_score=..., class_names=...)` so the split/leaf rules shown in slides match the actual sklearn tree. Pass the fitted model instance or fitted Pipeline as the first positional argument; never pass `DecisionTreeClassifier`, `DecisionTreeRegressor`, aliases of those classes, `.tree_`, `model_or_pipeline=...`, or an unfitted class/property. Include compact rule strings in `analysis_summary['decision_tree_rules']` and `business_findings`.
- `build_sklearn_tree_artifact` and `render_decision_tree_rules_figure` are already available in the analysis runtime. Call them directly; do not import them from `sklearn_utils`, `analytics_helpers`, or any other helper module.
- Do not inspect sklearn tree internals yourself. Do not use `DecisionTreeClassifier.tree_`, `DecisionTreeRegressor.tree_`, aliases such as `DTC.tree_`, `.feature`, `.threshold`, `.value`, or `.values` to build rules. Train a model instance or Pipeline, then pass that fitted object to `build_sklearn_tree_artifact(...)`; if using a Pipeline, `feature_names=None` is acceptable because the helper can infer transformed feature names.
- Save the decision tree diagram as `decision_tree_rules.png` after the EDA figures, just like the normal code-generated chart PNGs. The diagram must show split nodes and leaf prediction/rule nodes as rectangles connected with lines, so PDF and slides can reuse the saved image instead of rebuilding the tree themselves.
- Call `render_decision_tree_rules_figure(decision_tree_artifact, 'decision_tree_rules.png')` after creating the model artifact. Add `figure_captions['decision_tree_rules.png']`.
- Add one `analysis_artifacts` item for the model rules with `chart_type='decision_tree'`, `artifact_id='decision_tree_rules'`, `artifact_type='chart_spec'`, `slide_candidate=True`, `fallback_path='decision_tree_rules.png'`, the target, model_type, train/test metric labels/values, `data.model_verified=True`, `data.rules_match_model=True`, and a small `data` payload containing `nodes` and `edges`. Split nodes must contain feature-threshold labels from the fitted model. Leaf nodes must contain prediction/class/value plus the readable rule/path leading to that leaf.
- Decision tree `data.edges` must connect split nodes to leaf/split nodes and include true/false branch labels. Do not create an artifact with only leaves or empty edges.
- Do not claim causality unless the plan and data context support a credible causal design.
- For lending recommendations or summaries, describe policy changes as validation pilots with target segment, evidence, pilot metric, and governance/fairness caveat. Do not claim reduced credit losses unless the analysis explicitly calculates the reduction.
- Handle missing values, type conversion, outliers, identifiers, free text, near-constant columns, and categorical long tails deliberately before analysis.
- For pandas forward/backward fill, use `.ffill()` and `.bfill()` instead of deprecated `fillna(method=...)`.
- For pandas time grouping, avoid deprecated or version-fragile offset aliases in `resample`, `Grouper`, and `date_range`.
  Use pandas-safe aliases such as `h` for hourly, `D` for daily, `W` for weekly, `ME` for month-end, `QE` for quarter-end, and `YE` for year-end.
  Do not use bare `H`, `M`, or `Y` as offset frequencies; if you only need display labels, use `.dt.to_period('M').astype(str)` instead of resampling with `M`.
- Do not call private sklearn or pandas internals. For imputation, use public `fit_transform` on selected numeric columns or simple median/mode fills.
- Parse compact numeric strings before numeric analysis: handle commas, percentages, and suffixes such as K, M, and B with a small helper, then use `pd.to_numeric(..., errors='coerce')`.
- When using `pd.cut`, make labels after the final bin edge list so `len(labels) == len(bin_edges) - 1`; if bins are dynamic, omit labels and rename categories after cutting.
- Define `bin_edges` or `bins` before every `pd.cut` call and use one variable name consistently; skip bin-based visuals if the metric has too few unique values.
- When using `pd.qcut(..., duplicates='drop')`, do not pass a fixed labels list unless you compute the actual dropped bin count first; use `labels=False` or category-derived labels when in doubt.
- Save charts as figure_1.png, figure_2.png, etc. so the PDF and EDA slides can use the same code-generated visuals. When decision tree modeling runs, also save the model diagram as decision_tree_rules.png.
- Save figures directly by those file names; do not import `os`, `pathlib`, or other filesystem helpers in generated analysis code.
- Style saved charts for comfortable slide reading: use a light neutral background, high-contrast labels, and a small muted colorblind-aware palette using valid matplotlib colors such as `#1f4e79`, `#2a9d8f`, `#e9c46a`, `#4f772d`, and `firebrick`. Avoid invalid color names with spaces, neon colors, rainbow palettes, and red/green-only distinctions.
- Make every PNG slide-friendly: one readable chart per image, large labels, visible annotations for the claimed takeaway, and a title that matches the visible evidence. For stock/time-series risk, prefer price trend, volume spikes, return distribution, and seasonality; use at most one rolling/windowed volatility or drawdown risk figure unless explicitly requested.
- Create charts that explain trends, comparisons, drivers, or risks.
- Build chart payloads from aligned aggregated rows: category labels, plotted values, annotation labels, and color sequences must have matching lengths.
- When building artifact data, iterate over grouped rows or records. If a metric is a scalar, wrap it as one row such as `{{'label': 'Metric', 'value': value}}` instead of iterating the scalar.
- Use explicit valid matplotlib/seaborn color strings or one valid color per plotted group. Avoid partial color arrays assembled from differently sized subsets.
- Convert pandas categorical labels to strings before concatenation, and cast metrics to numeric before arithmetic or mean/rate aggregation. Use named aggregations on selected metric columns; do not call `.mean()` on a mixed or categorical dataframe.
- Build an `analysis_artifacts` list with at least four slide-worthy visuals so slides 4-7 still have structured chart fallback data. Each artifact must represent the chart as data + meaning, not just an image path.
- Each chart artifact should include: artifact_id, artifact_type='chart_spec', slide_candidate, finding, chart_type, title, x_label, y_label, series or data, takeaway, and recommended_template.
- Use chart_type values such as bar, grouped_bar, horizontal_bar, ranking, line, scatter, small_multiples_bar, distribution, metric_cards, or comparison.
- Use simple aggregated data rows such as category/value, label/value, x/y, or grouped rows with group_by. Do not provide only prose findings.
- For small multiples, provide series as a list of named mini-chart series with readable data points.
- Give each chart a clear title, axis labels, and legend when needed
- Build an `analysis_summary` dict with business-facing findings and numeric evidence
- Include `user_goal_alignment` in `analysis_summary` explaining how the outputs answer the user's description.
- Build a `business_findings` list with concise, evidence-backed bullet points for reporting
- Build a `figure_captions` dict mapping figure file names to one-sentence business interpretations
- Optionally also build a backward-compatible `chart_specs` list for simple reusable visuals. Use only aggregated data, not full raw dataframes.
- Each chart spec should include: id, chart_type, title, takeaway, x, y, optional series, and data as a short list of row dictionaries.
- Build an `analysis_summary` dict with technical findings and numeric evidence that a downstream business translator can interpret
- Avoid Unicode symbols in print/debug text; prefer no print calls, and keep all script text ASCII-safe.
- Use try-except around major blocks
- Keep the script complete and bounded. Prefer 3 to 5 focused figures and concise helpers over an overlong response that truncates before the required output assignments.
- Assign the required output contract before optional modeling or secondary visuals so a complete response remains runnable.
- Use only approved analytics packages. Prefer pandas, numpy, matplotlib, seaborn, scipy, statsmodels, sklearn, plotly, and pillow/PIL.
- Do not use subprocess, sys, requests, socket, shutil, pip, open(), eval(), exec(), or runtime package installation.
- If review feedback says a package was blocked, revise the analysis to use approved analytics packages instead.
- No markdown, no explanation"""
        corrective_note = ""
        last_candidate = ""
        for attempt in range(1, self._GENERATION_ATTEMPTS_PER_ITERATION + 1):
            self._report_generation_progress(
                progress_callback,
                f"waiting for model code (iteration {iteration}, attempt {attempt}/{self._GENERATION_ATTEMPTS_PER_ITERATION})",
            )
            raw = self.openrouter_client.chat_completion(
                self._system_prompt("Return Python code only."),
                f"{prompt}\n\n{corrective_note}".strip(),
                max_retries=2,
                max_tokens=8000,
            )
            candidate = self._extract_code(raw)
            if self._looks_like_analysis_script(candidate):
                self._report_generation_progress(
                    progress_callback,
                    f"model code received (iteration {iteration}, attempt {attempt}/{self._GENERATION_ATTEMPTS_PER_ITERATION})",
                )
                return candidate
            last_candidate = candidate or raw.strip()
            syntax_error = self._python_syntax_error(last_candidate)
            corrective_note = self._format_syntax_corrective_note(last_candidate, syntax_error)
            self._report_generation_progress(
                progress_callback,
                f"model code needs revision (iteration {iteration}, attempt {attempt}/{self._GENERATION_ATTEMPTS_PER_ITERATION})",
            )
        detail = corrective_note.splitlines()[0] if corrective_note else "Response did not match the analysis code contract."
        raise RuntimeError(
            "Model did not return a valid analysis script after "
            f"{self._GENERATION_ATTEMPTS_PER_ITERATION} attempts in coding iteration {iteration}. {detail}"
        )

    def _report_generation_progress(self, callback: Any, status: str) -> None:
        if callable(callback):
            callback(status)
        self._logger.info("%s", status)

    def _decision_tree_prompt_context(self, target_column: str, csv_data: dict[str, pd.DataFrame]) -> str:
        if not target_column:
            return "No target column was provided. Do not train a decision tree model."
        matches: list[dict[str, Any]] = []
        for name, df in csv_data.items():
            if target_column not in df.columns:
                continue
            series = df[target_column]
            matches.append(
                {
                    "dataset": name,
                    "target_column": target_column,
                    "target_dtype": str(series.dtype),
                    "target_role_hint": self._generation_role_hint(target_column, series),
                    "target_missing_pct": round(float(series.isna().mean() * 100), 2),
                    "target_unique_count": int(series.nunique(dropna=True)),
                    "available_feature_columns": [str(column) for column in df.columns if str(column) != target_column][:60],
                }
            )
        if not matches:
            return (
                f"Requested target column '{target_column}' was not found in the loaded datasets. "
                "Do not train a decision tree model; record the skipped reason in analysis_summary."
            )
        return (
            "A target column was provided. Train exactly one interpretable decision tree model after EDA, "
            "using this target context. Fit a model or Pipeline instance, assign it to a variable, and pass that fitted object "
            "as the first positional argument to build_sklearn_tree_artifact; do not pass sklearn classes, .tree_ properties, "
            "model_or_pipeline=..., or manually read .value/.values from tree internals.\n"
            f"{json_dumps_safe(matches, indent=2)[:1800]}"
        )

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
            "Start with imports. Include analysis_summary, business_findings, figure_captions, analysis_artifacts, and optional chart_specs assignments. "
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

    def _dataset_generation_context(self, csv_data: dict[str, pd.DataFrame]) -> dict[str, Any]:
        context: dict[str, Any] = {}
        for name, df in csv_data.items():
            column_profiles: dict[str, dict[str, Any]] = {}
            for column in list(df.columns)[:60]:
                series = df[column]
                non_null = series.dropna()
                profile: dict[str, Any] = {
                    "dtype": str(series.dtype),
                    "role_hint": self._generation_role_hint(str(column), series),
                    "missing_pct": round(float(series.isna().mean() * 100), 2),
                    "unique_count": int(series.nunique(dropna=True)),
                    "sample_values": [str(value)[:80] for value in non_null.head(3).tolist()],
                }
                numeric = pd.to_numeric(series, errors="coerce").dropna()
                if not numeric.empty and numeric.count() >= max(1, int(non_null.count() * 0.8)):
                    profile["numeric_range"] = {
                        "min": round(float(numeric.min()), 4),
                        "median": round(float(numeric.median()), 4),
                        "max": round(float(numeric.max()), 4),
                    }
                column_profiles[str(column)] = profile
            context[name] = {
                "shape": list(df.shape),
                "duplicate_rows": int(df.duplicated().sum()),
                "profiled_columns": column_profiles,
                "omitted_column_count": max(int(len(df.columns) - len(column_profiles)), 0),
                "sample_rows": df.head(3).to_dict("records"),
            }
        return context

    def _suited_visual_plan(
        self,
        csv_data: dict[str, pd.DataFrame],
        analysis_plan: dict[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        plans: dict[str, list[dict[str, Any]]] = {}
        for name, df in csv_data.items():
            roles = {column: self._generation_role_hint(str(column), df[column]) for column in df.columns}
            target = self._target_column(df, roles)
            temporal_components = self._temporal_component_columns(df)
            has_split_datetime = self._has_split_datetime(temporal_components)
            datetime_cols = [column for column, role in roles.items() if role == "datetime"]
            numeric_cols = [
                column
                for column, role in roles.items()
                if (
                    role in {"numeric-continuous", "numeric-discrete"}
                    or (has_split_datetime and role == "binary" and self._is_weather_measure_column(str(column)))
                )
                and column not in temporal_components.values()
            ]
            categorical_cols = [
                column
                for column, role in roles.items()
                if role in {"binary", "nominal", "ordinal"} and column != target
            ]
            dataset_plan: list[dict[str, Any]] = []
            if datetime_cols and self._is_stock_time_series_dataset(df, roles):
                dataset_plan.extend(self._stock_time_series_visual_plan(datetime_cols, numeric_cols, list(df.columns)))
                plans[name] = dataset_plan[:5]
                continue
            if has_split_datetime and numeric_cols:
                dataset_plan.extend(self._split_datetime_visual_plan(temporal_components, numeric_cols))
            if target and categorical_cols:
                for column in categorical_cols[:2]:
                    dataset_plan.append(
                        {
                            "figure": len(dataset_plan) + 1,
                            "chart_type": "horizontal_bar",
                            "columns": [column, target],
                            "question": f"Which {column} segments have the highest {target} rate?",
                            "method": "grouped target rate or share-of-positive comparison",
                            "recommended_template": "horizontal_bar_ranking",
                        }
                    )
            if datetime_cols and numeric_cols:
                dataset_plan.append(
                    {
                        "figure": len(dataset_plan) + 1,
                        "chart_type": "line",
                        "columns": [datetime_cols[0], numeric_cols[0]],
                        "question": f"How does {numeric_cols[0]} change over {datetime_cols[0]}?",
                        "method": "time aggregation with mean, sum, or median based on metric meaning",
                        "recommended_template": "comparison_chart_with_interpretation",
                    }
                )
            if categorical_cols and numeric_cols:
                dataset_plan.append(
                    {
                        "figure": len(dataset_plan) + 1,
                        "chart_type": "bar",
                        "columns": [categorical_cols[0], numeric_cols[0]],
                        "question": f"How does {numeric_cols[0]} differ by {categorical_cols[0]}?",
                        "method": "ranked grouped mean or median with top categories only",
                        "recommended_template": "single_bar_chart_with_insight",
                    }
                )
            if len(numeric_cols) >= 2:
                dataset_plan.append(
                    {
                        "figure": len(dataset_plan) + 1,
                        "chart_type": "scatter",
                        "columns": numeric_cols[:2],
                        "question": f"What relationship exists between {numeric_cols[0]} and {numeric_cols[1]}?",
                        "method": "correlation or scatter plot with outlier-aware interpretation",
                        "recommended_template": "comparison_chart_with_interpretation",
                    }
                )
            if numeric_cols:
                dataset_plan.append(
                    {
                        "figure": len(dataset_plan) + 1,
                        "chart_type": "distribution",
                        "columns": [numeric_cols[0]],
                        "question": f"What does the distribution of {numeric_cols[0]} reveal about concentration or outliers?",
                        "method": "histogram or box plot only when spread affects the decision",
                        "recommended_template": "distribution_with_callout",
                    }
                )
            if not dataset_plan and categorical_cols:
                dataset_plan.append(
                    {
                        "figure": 1,
                        "chart_type": "horizontal_bar",
                        "columns": [categorical_cols[0]],
                        "question": f"Which {categorical_cols[0]} categories dominate the data?",
                        "method": "ranked frequency or share-of-total comparison",
                        "recommended_template": "horizontal_bar_ranking",
                    }
                )
            plans[name] = dataset_plan[:5]
        if isinstance(analysis_plan, dict) and analysis_plan.get("visualization_plan"):
            plans["_planner_visualization_plan"] = [
                {"planner_instruction": str(item)} for item in analysis_plan.get("visualization_plan", [])[:5]
            ]
        return plans

    def _is_stock_time_series_dataset(self, df: pd.DataFrame, roles: dict[str, str]) -> bool:
        columns = [str(column).lower().strip() for column in df.columns]
        has_date = any(role == "datetime" for role in roles.values())
        has_price = any(column in {"price", "close", "adj close", "last"} or "price" in column for column in columns)
        has_ohlc = {"open", "high", "low"} & set(columns)
        has_volume_or_return = any(
            token in " ".join(columns)
            for token in ("vol", "volume", "change", "return")
        )
        return bool(has_date and (has_price or has_ohlc) and has_volume_or_return)

    def _stock_time_series_visual_plan(
        self,
        datetime_cols: list[str],
        numeric_cols: list[str],
        columns: list[str],
    ) -> list[dict[str, Any]]:
        date_col = datetime_cols[0]
        price_col = self._preferred_column(columns, ("price", "close", "adj close", "last")) or (numeric_cols[0] if numeric_cols else "")
        volume_col = self._preferred_column(columns, ("vol.", "volume", "vol"))
        change_col = self._preferred_column(columns, ("change %", "change", "return"))
        plan: list[dict[str, Any]] = []
        if price_col:
            plan.append(
                {
                    "figure": len(plan) + 1,
                    "chart_type": "line",
                    "columns": [date_col, price_col],
                    "question": f"How has {price_col} moved over time, including trend and key support/resistance levels?",
                    "method": "sort chronologically, plot price with rolling average or key reference levels, and annotate major regimes",
                    "recommended_template": "comparison_chart_with_interpretation",
                }
            )
        if price_col and change_col:
            plan.append(
                {
                    "figure": len(plan) + 1,
                    "chart_type": "line",
                    "columns": [date_col, price_col, change_col],
                    "question": "Where are the highest volatility or drawdown periods?",
                    "method": "derive one slide-readable rolling risk chart with clear annotations or windowed bands; do not create separate dense volatility and drawdown slides unless explicitly requested",
                    "recommended_template": "comparison_chart_with_interpretation",
                }
            )
        if volume_col:
            plan.append(
                {
                    "figure": len(plan) + 1,
                    "chart_type": "line",
                    "columns": [date_col, volume_col],
                    "question": "When do volume or liquidity spikes appear, and how should they gate decisions?",
                    "method": "parse compact K/M/B volume strings, aggregate by date/month when needed, and label spike windows",
                    "recommended_template": "comparison_chart_with_interpretation",
                }
            )
        if change_col:
            plan.append(
                {
                    "figure": len(plan) + 1,
                    "chart_type": "distribution",
                    "columns": [change_col],
                    "question": "What does the return distribution imply about downside and upside risk?",
                    "method": "parse percentages, show distribution with quantile callouts, and avoid dense volume-return scatter as default",
                    "recommended_template": "distribution_with_callout",
                }
            )
            plan.append(
                {
                    "figure": len(plan) + 1,
                    "chart_type": "small_multiples_bar",
                    "columns": [date_col, change_col],
                    "question": "Which months or periods show materially different average returns?",
                    "method": "derive month/period labels from date and plot aggregated returns in calendar order",
                    "recommended_template": "small_multiples_with_takeaway",
                }
            )
        return plan

    def _preferred_column(self, columns: list[str], candidates: tuple[str, ...]) -> str:
        lower_map = {str(column).lower().strip(): str(column) for column in columns}
        for candidate in candidates:
            if candidate in lower_map:
                return lower_map[candidate]
        for column in columns:
            lowered = str(column).lower().strip()
            if any(candidate in lowered for candidate in candidates):
                return str(column)
        return ""

    def _temporal_component_columns(self, df: pd.DataFrame) -> dict[str, str]:
        components: dict[str, str] = {}
        aliases = {
            "year": {"year", "yr", "yyyy"},
            "month": {"mo", "mon", "month", "mm"},
            "day": {"dy", "day", "dd", "dayofmonth"},
            "hour": {"hr", "hour", "hh"},
        }
        for column in df.columns:
            normalized = re.sub(r"[^a-z0-9]", "", str(column).lower())
            for component, names in aliases.items():
                if normalized in names and component not in components:
                    components[component] = str(column)
        return components

    def _has_split_datetime(self, components: dict[str, str]) -> bool:
        return {"year", "month", "day"}.issubset(components)

    def _split_datetime_visual_plan(
        self,
        temporal_components: dict[str, str],
        numeric_cols: list[str],
    ) -> list[dict[str, Any]]:
        primary_metric = self._preferred_weather_metric(numeric_cols, ("t2m", "temperature", "temp")) or numeric_cols[0]
        solar_metric = self._preferred_weather_metric(numeric_cols, ("clrs", "sw", "solar", "radiation"))
        humidity_metric = self._preferred_weather_metric(numeric_cols, ("rh", "humidity"))
        wind_metric = self._preferred_weather_metric(numeric_cols, ("ws", "wind"))
        related_metric = humidity_metric or wind_metric or next((col for col in numeric_cols if col != primary_metric), "")
        plan: list[dict[str, Any]] = [
            {
                "figure": 1,
                "chart_type": "line",
                "columns": list(temporal_components.values()) + [primary_metric],
                "question": f"How does {primary_metric} trend over the full hourly period?",
                "method": "construct a timestamp with pd.to_datetime from year/month/day/hour components, then aggregate by month or year before plotting",
                "recommended_template": "comparison_chart_with_interpretation",
            },
            {
                "figure": 2,
                "chart_type": "small_multiples_bar",
                "columns": [temporal_components["month"], primary_metric],
                "question": f"What seasonal monthly profile is visible in {primary_metric}?",
                "method": "group by month and plot mean values in calendar order; keep month labels separate from numeric values",
                "recommended_template": "small_multiples_with_takeaway",
            },
        ]
        if "hour" in temporal_components and solar_metric:
            plan.append(
                {
                    "figure": 3,
                    "chart_type": "line",
                    "columns": [temporal_components["hour"], solar_metric],
                    "question": f"What diurnal cycle does {solar_metric} show by hour?",
                    "method": "group by hour and plot the mean solar value; do not average non-metric string labels",
                    "recommended_template": "comparison_chart_with_interpretation",
                }
            )
        if related_metric:
            plan.append(
                {
                    "figure": len(plan) + 1,
                    "chart_type": "scatter",
                    "columns": [primary_metric, related_metric],
                    "question": f"How does {related_metric} relate to {primary_metric}?",
                    "method": "coerce both metrics to numeric, drop missing pairs, use pandas correlation or scipy.stats without shadowing the stats module",
                    "recommended_template": "comparison_chart_with_interpretation",
                }
            )
        plan.append(
            {
                "figure": len(plan) + 1,
                "chart_type": "distribution",
                "columns": [primary_metric],
                "question": f"Which extreme or outlier ranges in {primary_metric} matter for operations?",
                "method": "use quantiles or IQR on numeric values; report threshold counts and rates as business findings",
                "recommended_template": "distribution_with_callout",
            }
        )
        return plan[:5]

    def _preferred_weather_metric(self, numeric_cols: list[str], terms: tuple[str, ...]) -> str:
        for column in numeric_cols:
            normalized = re.sub(r"[^a-z0-9]", "", str(column).lower())
            if any(term in normalized for term in terms):
                return str(column)
        return ""

    def _is_weather_measure_column(self, column: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]", "", column.lower())
        weather_terms = (
            "t2m",
            "temp",
            "temperature",
            "rh",
            "humidity",
            "ws",
            "wind",
            "clrs",
            "solar",
            "radiation",
            "sfc",
            "dwn",
        )
        return any(term in normalized for term in weather_terms)

    def _target_column(self, df: pd.DataFrame, roles: dict[str, str]) -> str:
        priority_terms = (
            "attrition",
            "default",
            "churn",
            "risk",
            "target",
            "label",
            "outcome",
            "status",
            "approved",
            "fraud",
        )
        for column in df.columns:
            name = str(column).lower()
            if roles.get(column) == "binary" and any(term in name for term in priority_terms):
                return str(column)
        for column in df.columns:
            if roles.get(column) == "binary":
                return str(column)
        return ""

    def _generation_role_hint(self, column: str, series: pd.Series) -> str:
        name = column.lower()
        non_null = series.dropna()
        unique_count = int(non_null.nunique())
        row_count = max(int(len(series)), 1)

        if 0 < unique_count <= 2:
            return "binary"
        if self._is_generation_identifier_name(name):
            return "identifier"
        if pd.api.types.is_datetime64_any_dtype(series):
            return "datetime"
        if pd.api.types.is_numeric_dtype(series):
            if unique_count <= min(20, max(5, int(row_count * 0.05))):
                return "numeric-discrete"
            return "numeric-continuous"

        sample = non_null.astype(str).str.strip().head(200)
        if not sample.empty:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                datetime_parse_rate = pd.to_datetime(sample, errors="coerce").notna().mean()
            if datetime_parse_rate >= 0.8:
                return "datetime"
            if pd.to_numeric(sample, errors="coerce").notna().mean() >= 0.8:
                return "numeric-continuous"
            if float(sample.str.len().mean()) > 80:
                return "free-text"

        ordinal_terms = {"low", "medium", "high", "very high", "poor", "fair", "good", "excellent"}
        observed = {value.lower() for value in sample.unique()}
        if observed and observed.issubset(ordinal_terms):
            return "ordinal"
        return "nominal"

    def _is_generation_identifier_name(self, name: str) -> bool:
        return (
            name == "id"
            or name.endswith(("_id", " id", "_key", " key"))
            or "uuid" in name
            or bool(re.search(r"(?:^|[_\s])(invoice|order|account|record)[_\s]?(?:id|key|no|number)$", name))
        )


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
        review_skill = _load_repo_skill_text("review-analysis-code")
        user_description = str(self.context.get("user_data_description", "")).strip()
        data_understanding = self.context.get("data_understanding", {})
        return self.openrouter_client.chat_completion_json(
            self._system_prompt("Review analytical quality, business fit, and chart usefulness."),
            f"Review this analysis code against the plan.\nANALYSIS SKILL:\n{analysis_skill[:1800]}\n"
            f"REVIEW SKILL:\n{review_skill[:1800]}\n"
            f"USER DESCRIPTION PARAMETER:\n{user_description or 'No user description provided.'}\n"
            f"DATA UNDERSTANDING OUTPUT:\n{json_dumps_safe(data_understanding, indent=2)[:1800]}\n"
            f"ITERATION: {iteration}\nPLAN:\n"
            f"{json_dumps_safe(analysis_plan, indent=2)[:1800]}\nCODE:\n{code[:4500]}\n"
            f"EXECUTION RESULT:\n{json_dumps_safe(execution or {}, indent=2)[:1800]}\n"
            f"ARTIFACT ISSUES:\n{json_dumps_safe(artifact_issues or [], indent=2)}\n"
            "Reject or request revision if the analysis lacks meaningful visuals, numeric evidence, "
            "business-ready findings, or clear alignment to the user's description. Also reject if visuals "
            "ignore the data-understanding column roles or use decorative/random chart choices.",
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
            self._system_prompt(
                "Translate technical analysis into business meaning and decision context. "
                "Use the user's description as the primary business lens."
                f"{self._project_skill_prompt('business_translation')}"
            ),
            f"Translate these results into business insights.\nDATA:\n{json_dumps_safe(data_context, indent=2)[:1800]}\n"
            f"MARKET:\n{json_dumps_safe(market_context, indent=2)[:1600]}\n"
            f"RESULTS:\n{json_dumps_safe(analysis_results, indent=2)[:2200]}\n"
            "Focus on why the analysis matters to the user's stated business context, "
            "what the evidence suggests, and what managers should do next.",
            schema,
        )


class DecisionMakerAgent(BaseAgent):
    def execute(self, all_outputs: dict[str, Any], analysis_results: dict[str, Any], business_insights: dict[str, Any]) -> dict[str, Any]:
        schema = {
            "title": "string",
            "executive_summary": "string",
            "decision_context": "string",
            "recommendations": [{"rank": "integer", "action": "string", "rationale": "string", "evidence": "string", "timeline": "string", "impact": "High/Medium/Low"}],
            "limitations": [{"limitation": "string", "mitigation": "string", "decision_impact": "string"}],
            "final_recommendation": "string",
            "conclusion": "string",
        }
        return self.openrouter_client.chat_completion_json(
            self._system_prompt(
                "Create a decision-ready executive report with strong business framing. "
                "Use the user's description to frame the decision context and recommendation criteria."
                f"{self._project_skill_prompt('recommendation_generation')}"
            ),
            f"Compile a decision report from:\nOUTPUTS:\n{json_dumps_safe(all_outputs, indent=2)[:2500]}\n"
            f"RESULTS:\n{json_dumps_safe(analysis_results, indent=2)[:1500]}\n"
            f"BUSINESS:\n{json_dumps_safe(business_insights, indent=2)[:1500]}\n"
            "Recommendations must say what to do, why to do it, what evidence supports it, "
            "and how it answers the user's stated context. Also include practical limitations with mitigation "
            "or validation steps so the slide deck can present limitations honestly.",
            schema,
        )


class PresentationArchitectAgent(BaseAgent):
    def execute(self, workflow_state: dict[str, Any]) -> dict[str, Any]:
        slide_skill = _load_repo_skill_text("generate-slide-deck")
        workflow_objective = workflow_state.get("workflow_objective", {}) or {}
        data_description = (
            workflow_state.get("data_description")
            or workflow_state.get("user_data_description")
            or (workflow_objective.get("raw_description") if isinstance(workflow_objective, dict) else "")
            or "No user description provided."
        )
        schema = {
            "deck_title": "string",
            "subtitle": "string",
            "audience": "executive",
            "theme": "consulting_minimal",
            "dataset_context": {
                "name": "string",
                "description": "string",
                "rows": "integer or null",
                "columns": "integer or null",
                "target": "string or null",
                "data_quality_notes": ["string"],
            },
            "slides": [
                {
                    "slide_number": "integer",
                    "slide_role": "title|data_understanding|market_context|analysis|findings|business_translation|recommendations|limitations|summary",
                    "template": "title_cover|data_understanding_overview|market_context_bullets|chart_left_insight_right|chart_right_insight_left|full_width_chart_takeaway|metric_strip_plus_chart|small_multiples_with_takeaway|single_bar_chart_with_insight|horizontal_bar_ranking|metric_cards_with_chart|comparison_chart_with_interpretation|distribution_with_callout|segment_profile_cards|three_finding_cards|comparison_matrix|recommendation_priority|limitations_professional|executive_summary_closing",
                    "headline": "insight-driven slide headline",
                    "main_message": "one clear takeaway",
                    "subtitle": "optional short context",
                    "content_blocks": [{"type": "bullets|recommendations|limitations", "items": ["short supporting point"]}],
                    "visual": {
                        "type": "code_figure|structured_chart|legacy_image_fallback",
                        "chart_type": "bar|column|grouped_bar|line|scatter|horizontal_bar|ranking|small_multiples_bar|distribution|metric_cards|comparison|decision_tree|legacy_image",
                        "artifact_id": "id from analysis_results.analysis_artifacts",
                        "image_path": "path from CODE FIGURES FOR EDA SLIDES AND PDF when using a code figure",
                        "takeaway": "what the viewer should conclude from the visual",
                        "fallback_reason": "blank unless this uses legacy image fallback",
                    },
                }
            ],
        }
        return self.openrouter_client.chat_completion_json(
            self._system_prompt(
                "Create a concise executive deck structure in a consulting style. "
                "Use the user's description as the deck objective and audience context. "
                "Create a top-down story instead of a repetitive report template."
            ),
            f"DATA DESCRIPTION / BUSINESS CONTEXT:\n{data_description}\n"
            f"WORKFLOW OBJECTIVE:\n{json_dumps_safe(workflow_objective, indent=2)[:1200]}\n"
            f"SLIDE SKILL:\n{slide_skill[:1800]}\n"
            f"Design a slide deck from this workflow state:\n{json_dumps_safe(workflow_state.get('agent_outputs', {}), indent=2)[:2800]}\n"
            f"RECOMMENDATIONS FOR SLIDE 10:\n{json_dumps_safe(workflow_state.get('agent_outputs', {}).get('decision_maker', {}).get('recommendations', []), indent=2)[:1800]}\n"
            f"LIMITATIONS / RISKS FOR SLIDE 11:\n{json_dumps_safe(_slide_limitations_payload(workflow_state), indent=2)[:1800]}\n"
            f"STRUCTURED ANALYSIS ARTIFACTS:\n{json_dumps_safe(workflow_state.get('analysis_results', {}).get('analysis_artifacts', []), indent=2)[:2600]}\n"
            f"CHART SPECS:\n{json_dumps_safe(workflow_state.get('analysis_results', {}).get('chart_specs', []), indent=2)[:1800]}\n"
            f"CODE FIGURES FOR EDA SLIDES AND PDF:\n{json_dumps_safe(workflow_state.get('saved_figures', []), indent=2)}\n"
            "Return compact JSON. Omit blank optional fields and do not repeat report paragraphs. "
            "Create a 12-slide top-down consulting story in this exact order: title, data understanding, market/domain context, four visual EDA analysis slides, evidence findings, business translation, recommendations, limitations, and ending summary. "
            "Use the template field and vary analysis templates. "
            "The LLM controls slide role, template, content blocks, chart intent, and emphasis. Do not output PowerPoint coordinates, sizes, fonts, or pixel positions. "
            "Slides 4-7 must use CODE FIGURES FOR EDA SLIDES AND PDF when those code-saved figures exist so the deck matches the PDF visuals. "
            "Use STRUCTURED ANALYSIS ARTIFACTS as visual fallback data only when a code-saved figure is unavailable. "
            "Slides 4-7 must be visual-first: code-generated EDA chart plus concise insight, not text-only findings when visuals exist. "
            "Analysis-slide bullets must state evidence or implication, not meta-instructions telling the reader to interpret or translate the visual. "
            "Use slide 8 for evidence findings and slide 9 for business meaning, so the findings slide summarizes the evidence before recommendations. "
            "Use image_path or visual_path only for actual CODE FIGURES listed above; do not invent figure paths. "
            "Headlines must be insight-driven and specific, not generic labels like Analysis, Results, Chart, or Data. "
            "Keep content_blocks concise: maximum 3 short business-facing bullets per slide. Visual objects should reference artifact ids or code figure paths, not repeat full chart datasets.",
            schema,
        )


def _slide_limitations_payload(workflow_state: dict[str, Any]) -> dict[str, Any]:
    outputs = workflow_state.get("agent_outputs", {}) or {}
    objective = workflow_state.get("workflow_objective", {}) or {}
    analysis = workflow_state.get("analysis_results", {}) or {}
    return {
        "decision_limitations": outputs.get("decision_maker", {}).get("limitations", []),
        "business_risks": outputs.get("business_translator", {}).get("risks", []),
        "objective_limitations": objective.get("limitations", []) if isinstance(objective, dict) else [],
        "analysis_artifact_warnings": workflow_state.get("analysis_artifact_warnings", []),
        "analysis_summary": analysis.get("analysis_summary", {}),
    }
