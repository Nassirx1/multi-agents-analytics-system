"""Versioned deterministic benchmark catalog for both analytics output paths."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from analytics_workflow.workflow_steps import ANALYTICS_REPORT_STEPS, HTML_DASHBOARD_STEPS

from .rubrics import OUTPUT_QUALITY_DIMENSIONS, REQUIRED_HARD_GATES


BENCHMARK_CATALOG_VERSION = "1.0.0"
_ID_PATTERN = re.compile(r"^maa\.[a-z0-9_]+\.[a-z0-9_]+\.v[1-9][0-9]*$")
_ROUTES = {"analytics_report", "html_dashboard", "shared"}


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    """An immutable benchmark definition consumed by the benchmark runner."""

    id: str
    name: str
    category: str
    target: str
    routes: tuple[str, ...]
    stages: tuple[str, ...]
    dimensions: tuple[str, ...]
    timeout_seconds: int
    fixture: Mapping[str, Any]
    expected: Mapping[str, Any]
    hard_gates: tuple[str, ...] = ()
    description: str = ""
    tags: tuple[str, ...] = ()
    deterministic: bool = True
    credentials_required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "routes", tuple(self.routes))
        object.__setattr__(self, "stages", tuple(self.stages))
        object.__setattr__(self, "dimensions", tuple(self.dimensions))
        object.__setattr__(self, "hard_gates", tuple(self.hard_gates))
        object.__setattr__(self, "tags", tuple(self.tags))
        object.__setattr__(self, "fixture", _freeze_mapping(self.fixture))
        object.__setattr__(self, "expected", _freeze_mapping(self.expected))

    @property
    def check(self) -> str:
        """Compatibility alias for runners that name evaluator registry keys checks."""

        return self.target

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "target": self.target,
            "routes": list(self.routes),
            "stages": list(self.stages),
            "dimensions": list(self.dimensions),
            "timeout_seconds": self.timeout_seconds,
            "fixture": _thaw(self.fixture),
            "expected": _thaw(self.expected),
            "hard_gates": list(self.hard_gates),
            "description": self.description,
            "tags": list(self.tags),
            "deterministic": self.deterministic,
            "credentials_required": self.credentials_required,
        }


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _case(
    id_suffix: str,
    name: str,
    category: str,
    target: str,
    *,
    routes: tuple[str, ...],
    stages: tuple[str, ...] = (),
    dimensions: tuple[str, ...] = (),
    timeout_seconds: int = 30,
    fixture: Mapping[str, Any] | None = None,
    expected: Mapping[str, Any] | None = None,
    hard_gates: tuple[str, ...] = (),
    description: str = "",
    tags: tuple[str, ...] = (),
) -> BenchmarkCase:
    return BenchmarkCase(
        id=f"maa.{category}.{id_suffix}.v1",
        name=name,
        category=category,
        target=target,
        routes=routes,
        stages=stages,
        dimensions=dimensions,
        timeout_seconds=timeout_seconds,
        fixture=fixture or {},
        expected=expected or {},
        hard_gates=hard_gates,
        description=description,
        tags=tags,
    )


_CATALOG = (
    _case(
        "choice_before_credentials",
        "Initial choice precedes credentials and datasets",
        "routing",
        "routing.initial_choice_before_credentials",
        routes=("shared",),
        fixture={"selections": ["invalid", "2"]},
        expected={"event_order": ["menu", "route", "credentials", "dataset"], "max_invalid_attempts": 3},
        hard_gates=("critical_workflow_free",),
        description="Proves the requested route is fixed before secrets, files, or agents are loaded.",
        tags=("credentials", "menu", "isolation"),
    ),
    _case(
        "analytics_branch_isolation",
        "Analytics route excludes dashboard runtime",
        "routing",
        "routing.analytics_branch_isolation",
        routes=("analytics_report",),
        expected={"forbidden_artifacts": [".html_dashboard", "dashboard.html"], "required_exporters": ["pdf", "slide_deck"]},
        hard_gates=("critical_workflow_free",),
        tags=("branch_isolation", "regression"),
    ),
    _case(
        "dashboard_branch_isolation",
        "HTML route excludes report exporters and unrelated agents",
        "routing",
        "routing.dashboard_branch_isolation",
        routes=("html_dashboard",),
        expected={"agents": ["Data Understander", "Dashboard Planning Agent", "HTML Dashboard Generator", "HTML Dashboard QA Agent"], "forbidden_artifacts": [".pdf", ".pptx", ".pbip", ".pbir"]},
        hard_gates=("critical_workflow_free",),
        tags=("branch_isolation", "least_privilege"),
    ),
    _case(
        "legacy_resume_route",
        "Legacy checkpoints resume safely",
        "routing",
        "routing.legacy_checkpoint_resume",
        routes=("shared",),
        fixture={"legacy_routes": [None, "power_bi"]},
        expected={"resolved_routes": ["analytics_report", "html_dashboard"], "preserve_completed_stages": True},
        hard_gates=("critical_workflow_free", "evidence_consistent"),
        tags=("resume", "compatibility"),
    ),
    _case(
        "profile_contract",
        "Data Understander profiles schema and quality",
        "agent",
        "agent.data_understander",
        routes=("analytics_report", "html_dashboard"),
        stages=("Data Understander",),
        fixture={"dataset": "mixed_types_with_missing_values"},
        expected={"required": ["row_count", "columns", "types", "missingness", "numeric_summary", "categorical_summary", "limitations"], "exact_row_count": True},
        hard_gates=("calculations_valid",),
        dimensions=("trustworthiness", "readability"),
        tags=("schema", "data_quality", "shared_stage"),
    ),
    _case(
        "source_traceability",
        "Market Researcher preserves source provenance",
        "agent",
        "agent.market_researcher",
        routes=("analytics_report",),
        stages=("Market Researcher",),
        fixture={"search_results": "stubbed_authoritative_and_low_quality_sources"},
        expected={"source_index_stable": True, "url_required": True, "claim_source_link_required": True, "low_quality_source_not_primary": True},
        hard_gates=("unsupported_claims_absent",),
        dimensions=("trustworthiness",),
        tags=("citations", "provenance", "stubbed_network"),
    ),
    _case(
        "feasible_analysis_plan",
        "Analysis Planner uses observed fields and feasible methods",
        "agent",
        "agent.analysis_planner",
        routes=("analytics_report",),
        stages=("Analysis Planner",),
        fixture={"profile": "mixed_types_without_requested_target"},
        expected={"unknown_columns": 0, "methods_match_types": True, "fallback_or_limitation_recorded": True, "acceptance_checks_present": True},
        hard_gates=("calculations_valid", "unsupported_claims_absent"),
        dimensions=("trustworthiness", "executive_suitability"),
        tags=("planning", "schema_grounding"),
    ),
    _case(
        "safe_reproducible_analysis",
        "Data Scientist Coder executes reproducibly",
        "agent",
        "agent.data_scientist_coder",
        routes=("analytics_report",),
        stages=("Data Scientist Coder",),
        timeout_seconds=90,
        fixture={"dataset": "classification_with_imbalance", "seed": 17},
        expected={"executes": True, "fixed_seed": True, "source_files_unchanged": True, "figures_exist": True, "metrics_include": ["test_accuracy", "baseline_accuracy", "balanced_accuracy", "precision", "recall", "f1"]},
        hard_gates=("critical_workflow_free", "calculations_valid", "sensitive_data_protected"),
        dimensions=("trustworthiness", "visibility"),
        tags=("code", "reproducibility", "model_metrics"),
    ),
    _case(
        "reviewer_detects_and_repairs",
        "Code Reviewer identifies seeded analytical defects",
        "agent",
        "agent.code_reviewer",
        routes=("analytics_report",),
        stages=("Code Reviewer",),
        fixture={"candidate": "seeded_leakage_wrong_denominator_and_missing_seed"},
        expected={"detects": ["target_leakage", "wrong_denominator", "missing_seed"], "approval_before_blockers_fixed": False, "repair_limit_respected": True},
        hard_gates=("calculations_valid", "critical_workflow_free"),
        dimensions=("trustworthiness",),
        tags=("negative_fixture", "repair_loop"),
    ),
    _case(
        "evidence_preserving_translation",
        "Business Translator preserves evidence meaning",
        "agent",
        "agent.business_translator",
        routes=("analytics_report",),
        stages=("Business Translator",),
        fixture={"findings": "supported_findings_with_eda_model_boundary"},
        expected={"numbers_unchanged": True, "evidence_ids_preserved": True, "correlation_not_causation": True, "plain_language": True},
        hard_gates=("unsupported_claims_absent", "evidence_consistent"),
        dimensions=("readability", "trustworthiness", "executive_suitability"),
        tags=("translation", "claim_integrity"),
    ),
    _case(
        "actionable_bounded_recommendations",
        "Decision Maker creates bounded actions",
        "agent",
        "agent.decision_maker",
        routes=("analytics_report",),
        stages=("Decision Maker",),
        fixture={"evidence": "mixed_supported_and_unsupported_actions"},
        expected={"required_action_fields": ["owner", "trigger", "timeline", "metric", "stop_condition", "evidence_ids", "validation_status"], "unsupported_actions": 0, "prioritized": True},
        hard_gates=("unsupported_claims_absent", "high_stakes_safety_clear"),
        dimensions=("trustworthiness", "executive_suitability"),
        tags=("decisions", "actionability", "safety"),
    ),
    _case(
        "pdf_generation_and_semantic_qa",
        "PDF generator produces a readable trusted artifact",
        "generator",
        "generator.pdf_report",
        routes=("analytics_report",),
        stages=("PDF Report Generator",),
        timeout_seconds=120,
        fixture={"workflow_state": "complete_small_report_state"},
        expected={"openable": True, "page_count_min": 2, "page_numbers": True, "semantic_qa": "pass", "evidence_hash_matches": True},
        hard_gates=("artifacts_openable", "artifact_qa_passed", "evidence_consistent", "primary_visuals_render"),
        dimensions=OUTPUT_QUALITY_DIMENSIONS,
        tags=("pdf", "render", "legacy_gate_17"),
    ),
    _case(
        "slide_generation_and_geometry_qa",
        "Slide generator produces an executive visual story",
        "generator",
        "generator.slide_deck",
        routes=("analytics_report",),
        stages=("Slide Deck Generator",),
        timeout_seconds=120,
        fixture={"workflow_state": "complete_small_report_state"},
        expected={"openable": True, "required_roles_present": True, "geometry_qa": "pass", "semantic_qa": "pass", "evidence_hash_matches": True},
        hard_gates=("artifacts_openable", "artifact_qa_passed", "evidence_consistent", "primary_visuals_render"),
        dimensions=OUTPUT_QUALITY_DIMENSIONS,
        tags=("pptx", "geometry", "legacy_gate_18"),
    ),
    _case(
        "source_grounded_dashboard_plan",
        "Dashboard planner selects real fields and concise pages",
        "agent",
        "agent.dashboard_planning",
        routes=("html_dashboard",),
        stages=("Dashboard Planning Agent",),
        fixture={"profile": "mixed_types_with_missing_values"},
        expected={"unknown_fields": 0, "pages_max": 3, "charts_per_page_max": 2, "kpis_max": 6, "filters_max": 3, "fallback_valid": True},
        hard_gates=("calculations_valid", "unsupported_claims_absent"),
        dimensions=("readability", "trustworthiness", "executive_suitability"),
        tags=("dashboard", "planning", "density"),
    ),
    _case(
        "self_contained_interactive_html",
        "HTML generator produces an offline interactive dashboard",
        "generator",
        "generator.html_dashboard",
        routes=("html_dashboard",),
        stages=("HTML Dashboard Generator",),
        timeout_seconds=60,
        fixture={"plan": "valid_two_page_dashboard", "dataset": "mixed_types_with_missing_values"},
        expected={"self_contained": True, "external_requests": 0, "filters_update_kpis_and_charts": True, "responsive": True, "semantic_fallback": True},
        hard_gates=("artifacts_openable", "primary_visuals_render", "sensitive_data_protected"),
        dimensions=OUTPUT_QUALITY_DIMENSIONS,
        tags=("html", "offline", "interaction"),
    ),
    _case(
        "qa_rejects_broken_dashboard",
        "Dashboard QA rejects blank, external, and over-dense output",
        "agent",
        "agent.html_dashboard_qa",
        routes=("html_dashboard",),
        stages=("HTML Dashboard QA Agent",),
        fixture={"mutations": ["blank_payload", "external_script", "three_charts_on_page", "missing_filter_handler"]},
        expected={"all_mutations_rejected": True, "clean_control_passes": True, "diagnostics_identify_mutation": True},
        hard_gates=("artifact_qa_passed", "primary_visuals_render"),
        dimensions=("visibility", "trustworthiness"),
        tags=("negative_fixture", "html", "qa"),
    ),
    _case(
        "analytics_offline_e2e",
        "Analytics report path completes with stubbed agents",
        "workflow",
        "workflow.analytics_report_e2e",
        routes=("analytics_report",),
        stages=tuple(ANALYTICS_REPORT_STEPS),
        timeout_seconds=180,
        fixture={"dataset": "small_business_classification", "clients": "deterministic_stubs"},
        expected={"status": "completed", "stage_order": list(ANALYTICS_REPORT_STEPS), "artifacts": ["pdf", "slide_deck", "evidence_bundle", "quality_receipt"], "dashboard_constructed": False},
        hard_gates=REQUIRED_HARD_GATES,
        dimensions=OUTPUT_QUALITY_DIMENSIONS,
        tags=("end_to_end", "offline", "report"),
    ),
    _case(
        "dashboard_offline_e2e",
        "HTML dashboard path completes with a stubbed planner",
        "workflow",
        "workflow.html_dashboard_e2e",
        routes=("html_dashboard",),
        stages=tuple(HTML_DASHBOARD_STEPS),
        timeout_seconds=90,
        fixture={"dataset": "small_business_classification", "clients": "deterministic_stubs"},
        expected={"status": "completed", "stage_order": list(HTML_DASHBOARD_STEPS), "artifacts": ["dashboard.html", "dashboard_plan.json", "dashboard_qa_receipt.json", "dashboard_success_receipt.json"], "report_exporters_called": False},
        hard_gates=REQUIRED_HARD_GATES,
        dimensions=OUTPUT_QUALITY_DIMENSIONS,
        tags=("end_to_end", "offline", "dashboard"),
    ),
    _case(
        "checkpoint_resume_integrity",
        "Checkpoint resume preserves completed evidence",
        "reliability",
        "reliability.checkpoint_resume",
        routes=("analytics_report", "html_dashboard"),
        fixture={"interrupt_after_stage": True},
        expected={"completed_stage_not_repeated": True, "route_preserved": True, "artifact_hashes_preserved": True},
        hard_gates=("critical_workflow_free", "evidence_consistent"),
        dimensions=("trustworthiness",),
        tags=("resume", "integrity"),
    ),
    _case(
        "secret_redaction",
        "Credentials never appear in logs or artifacts",
        "reliability",
        "reliability.secret_redaction",
        routes=("analytics_report", "html_dashboard"),
        fixture={"sentinel_secrets": ["OPENROUTER_SENTINEL", "BRAVE_SENTINEL"]},
        expected={"sentinel_matches": 0, "credentials_source": "environment"},
        hard_gates=("sensitive_data_protected",),
        dimensions=("trustworthiness",),
        tags=("security", "logs", "environment"),
    ),
    _case(
        "timeout_and_retry_bounds",
        "Timeouts and retry limits remain bounded",
        "reliability",
        "reliability.timeout_retry_bounds",
        routes=("analytics_report", "html_dashboard"),
        fixture={"transient_failures": 4, "deterministic_failure": True},
        expected={"transient_attempts_max": 3, "deterministic_attempts": 1, "stable_error_code": True},
        hard_gates=("critical_workflow_free",),
        tags=("timeout", "retry", "failure_mapping"),
    ),
    _case(
        "four_dimension_output_quality",
        "Outputs satisfy the four evidence-backed quality dimensions",
        "quality",
        "quality.four_dimension_score",
        routes=("analytics_report", "html_dashboard"),
        dimensions=OUTPUT_QUALITY_DIMENSIONS,
        fixture={"artifacts": "runner_selected_route_outputs", "rubric_version": "1.0.0"},
        expected={"overall_minimum": 85.0, "dimension_minimums": {"readability": 70.0, "visibility": 70.0, "trustworthiness": 75.0, "executive_suitability": 70.0}, "all_criteria_reported": True, "evidence_required": True, "provenance_required": True},
        hard_gates=REQUIRED_HARD_GATES,
        description="Produces separate 0-100 scores, weighted overall score, evidence, diagnostics, and hard-gate results.",
        tags=("readability", "visibility", "trustworthiness", "executive"),
    ),
    _case(
        "cross_artifact_consistency",
        "Final artifacts share one evidence story",
        "quality",
        "quality.cross_artifact_consistency",
        routes=("analytics_report",),
        fixture={"artifacts": ["pdf", "slide_deck", "report_outline", "slide_plan", "evidence_bundle"]},
        expected={"evidence_hash_match": True, "material_numeric_conflicts": 0, "recommendation_conflicts": 0},
        hard_gates=("evidence_consistent", "unsupported_claims_absent"),
        dimensions=("trustworthiness", "executive_suitability"),
        tags=("synchronization", "legacy_gate_16"),
    ),
    _case(
        "held_out_metamorphic_controls",
        "Quality scoring resists keyword and ordering games",
        "quality",
        "quality.held_out_metamorphic_controls",
        routes=("analytics_report", "html_dashboard"),
        fixture={
            "held_out": True,
            "variants": [
                "record_order_permutation",
                "whitespace_only_change",
                "absolute_path_relocation",
                "rubric_keyword_injection_without_evidence",
                "evidence_id_corruption",
                "blank_primary_visual",
                "clipped_primary_visual",
                "secret_leakage",
                "wrong_route_artifact",
                "plausible_but_unrelated_chart",
            ],
        },
        expected={
            "record_order_score_delta_max": 0.1,
            "whitespace_score_delta_max": 0.1,
            "path_relocation_score_delta_max": 0.1,
            "keyword_injection_score_gain_max": 0.0,
            "evidence_corruption_fails_gate": True,
            "blank_visual_fails_gate": True,
            "clipped_visual_reduces_visibility": True,
            "secret_leakage_fails_gate": True,
            "wrong_route_artifact_fails_gate": True,
            "unrelated_chart_fails_trust": True,
        },
        hard_gates=("unsupported_claims_absent", "evidence_consistent", "primary_visuals_render"),
        dimensions=OUTPUT_QUALITY_DIMENSIONS,
        tags=("held_out", "metamorphic", "anti_gaming", "negative_fixture"),
    ),
)


def load_benchmark_catalog() -> tuple[BenchmarkCase, ...]:
    """Return the immutable catalog in its stable execution order."""

    return _CATALOG


def validate_benchmark_catalog(cases: Iterable[BenchmarkCase] | None = None) -> list[str]:
    """Return catalog errors; an empty list is the validation success gate."""

    selected = tuple(_CATALOG if cases is None else cases)
    errors: list[str] = []
    ids: set[str] = set()
    for case in selected:
        prefix = case.id
        if not _ID_PATTERN.fullmatch(case.id):
            errors.append(f"{prefix}: id must be a versioned immutable ID ending in .vN")
        if case.id in ids:
            errors.append(f"{prefix}: duplicate id")
        ids.add(case.id)
        if not case.target:
            errors.append(f"{prefix}: target is required")
        if not case.routes or any(route not in _ROUTES for route in case.routes):
            errors.append(f"{prefix}: invalid or missing routes")
        if case.timeout_seconds <= 0:
            errors.append(f"{prefix}: timeout_seconds must be positive")
        invalid_dimensions = set(case.dimensions) - set(OUTPUT_QUALITY_DIMENSIONS)
        if invalid_dimensions:
            errors.append(f"{prefix}: unknown dimensions {sorted(invalid_dimensions)}")
        if case.credentials_required or not case.deterministic:
            errors.append(f"{prefix}: core catalog cases must be deterministic and credential-free")
    covered_stages = {stage for case in selected for stage in case.stages}
    for stage in (*ANALYTICS_REPORT_STEPS, *HTML_DASHBOARD_STEPS):
        if stage not in covered_stages:
            errors.append(f"stage coverage missing: {stage}")
    quality_cases = [case for case in selected if case.target == "quality.four_dimension_score"]
    if len(quality_cases) != 1:
        errors.append("exactly one canonical four-dimension quality benchmark is required")
    elif set(quality_cases[0].dimensions) != set(OUTPUT_QUALITY_DIMENSIONS):
        errors.append("canonical quality benchmark must cover every output-quality dimension")
    return errors


def benchmark_catalog_manifest(cases: Iterable[BenchmarkCase] | None = None) -> dict[str, Any]:
    selected = tuple(_CATALOG if cases is None else cases)
    payload = [case.to_dict() for case in selected]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return {
        "catalog_version": BENCHMARK_CATALOG_VERSION,
        "benchmark_count": len(selected),
        "catalog_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "benchmarks": payload,
    }
