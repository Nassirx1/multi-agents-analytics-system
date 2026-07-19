"""Evidence-backed output-quality scoring for reports, slides, and HTML dashboards.

The rubric is intentionally fail-closed. A high numeric score cannot override a
failed hard gate, and a positive observation without evidence receives at most
half credit. This makes the score useful as an optimization target without
turning superficial keyword insertion into a winning strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence


OUTPUT_QUALITY_RUBRIC_VERSION = "1.0.0"
OUTPUT_QUALITY_DIMENSIONS = (
    "readability",
    "visibility",
    "trustworthiness",
    "executive_suitability",
)


@dataclass(frozen=True, slots=True)
class Criterion:
    signal: str
    label: str
    weight: int
    diagnostic: str


@dataclass(frozen=True, slots=True)
class DimensionRubric:
    name: str
    weight: int
    minimum_score: float
    criteria: tuple[Criterion, ...]


@dataclass(frozen=True, slots=True)
class Observation:
    value: float
    evidence: tuple[str, ...] = ()
    provenance: str = ""
    diagnostic: str = ""


@dataclass(frozen=True, slots=True)
class DimensionScore:
    name: str
    score: float
    minimum_score: float
    passed: bool
    criteria: tuple[Mapping[str, Any], ...]
    diagnostics: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": self.score,
            "minimum_score": self.minimum_score,
            "passed": self.passed,
            "criteria": [dict(item) for item in self.criteria],
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True, slots=True)
class QualityScore:
    rubric_version: str
    dimensions: Mapping[str, DimensionScore]
    overall_score: float
    overall_minimum: float
    hard_gates: Mapping[str, bool]
    failed_gates: tuple[str, ...]
    diagnostics: tuple[str, ...]
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "rubric_version": self.rubric_version,
            "dimensions": {name: score.to_dict() for name, score in self.dimensions.items()},
            "overall_score": self.overall_score,
            "overall_minimum": self.overall_minimum,
            "hard_gates": dict(self.hard_gates),
            "failed_gates": list(self.failed_gates),
            "diagnostics": list(self.diagnostics),
            "passed": self.passed,
        }


OUTPUT_QUALITY_RUBRICS: Mapping[str, DimensionRubric] = MappingProxyType(
    {
        "readability": DimensionRubric(
            name="readability",
            weight=20,
            minimum_score=70.0,
            criteria=(
                Criterion("clear_information_hierarchy", "Clear information hierarchy", 25, "Make the answer-first hierarchy explicit."),
                Criterion("concise_prose", "Concise, plain-language prose", 20, "Remove boilerplate and shorten dense passages."),
                Criterion("labels_units_defined", "Labels, units, and abbreviations are defined", 20, "Add complete labels, units, and definitions."),
                Criterion("logical_sequence", "Logical reading and story sequence", 20, "Reorder content into a coherent evidence-to-action story."),
                Criterion("scan_friendly_density", "Scan-friendly density", 15, "Reduce density and strengthen headings or whitespace."),
            ),
        ),
        "visibility": DimensionRubric(
            name="visibility",
            weight=20,
            minimum_score=70.0,
            criteria=(
                Criterion("no_overlap_or_clipping", "No overlap or clipping", 25, "Repair overlapping, clipped, or off-canvas elements."),
                Criterion("legible_text", "Text is legible at normal viewing size", 20, "Increase text size or reduce content density."),
                Criterion("sufficient_contrast", "Contrast and color remain distinguishable", 20, "Increase contrast and avoid color-only distinctions."),
                Criterion("primary_visuals_render", "Primary visuals render and contain data", 20, "Replace blank, broken, or placeholder visuals."),
                Criterion("layout_fits_medium", "Layout fits page, slide, or viewport", 15, "Use a layout suited to the target medium and viewport."),
            ),
        ),
        "trustworthiness": DimensionRubric(
            name="trustworthiness",
            weight=35,
            minimum_score=75.0,
            criteria=(
                Criterion("claims_trace_to_evidence", "Claims resolve to evidence identifiers", 30, "Attach resolvable evidence IDs to material claims."),
                Criterion("calculations_reconciled", "Metrics and calculations reconcile", 25, "Recalculate metrics and record scope and denominator."),
                Criterion("sources_disclosed", "Sources, scope, and provenance are disclosed", 20, "Disclose dataset, external sources, filters, and time basis."),
                Criterion("limitations_and_uncertainty", "Limitations and uncertainty are honest", 15, "State uncertainty, model limits, and omitted analyses."),
                Criterion("artifact_integrity_receipts", "Artifacts have integrity and QA receipts", 10, "Produce hashes and deterministic QA receipts."),
            ),
        ),
        "executive_suitability": DimensionRubric(
            name="executive_suitability",
            weight=25,
            minimum_score=70.0,
            criteria=(
                Criterion("answer_first", "Answer-first framing", 25, "Lead with the decision or conclusion."),
                Criterion("decision_relevant", "Evidence is tied to a decision", 25, "Explain why each key finding changes a decision."),
                Criterion("action_owner_timing", "Actions include owner and timing", 20, "Assign an owner, trigger, timeline, and next review."),
                Criterion("prioritized_tradeoffs", "Priorities and trade-offs are explicit", 15, "Rank actions and state impact, risk, and trade-offs."),
                Criterion("executive_scope_control", "Detail is appropriate for executives", 15, "Move technical detail to notes and retain decision context."),
            ),
        ),
    }
)


REQUIRED_HARD_GATES = (
    "critical_workflow_free",
    "unsupported_claims_absent",
    "high_stakes_safety_clear",
    "evidence_consistent",
    "calculations_valid",
    "artifacts_openable",
    "artifact_qa_passed",
    "primary_visuals_render",
    "sensitive_data_protected",
)

ROUTE_HARD_GATE_PROFILES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "analytics_report": REQUIRED_HARD_GATES
        + (
            "pdf_semantic_qa_passed",
            "slide_geometry_qa_passed",
            "report_slide_evidence_hash_match",
            "dashboard_runtime_absent",
        ),
        "html_dashboard": REQUIRED_HARD_GATES
        + (
            "html_self_contained",
            "dashboard_interactions_functional",
            "dashboard_qa_passed",
            "report_artifacts_absent",
        ),
    }
)

# 85/100 preserves the earlier 76/90 normalized gate (84.44%) instead of
# silently weakening it to a generic 80% threshold.
OVERALL_MINIMUM_SCORE = 85.0

OBSERVATION_ANCHORS: Mapping[float, str] = MappingProxyType(
    {
        0.0: "Absent, contradicted, broken, or no usable evidence.",
        0.25: "An isolated element works, but most of the criterion is unmet.",
        0.5: "Partially met with material gaps or inconsistent coverage.",
        0.75: "Substantially met with minor, bounded defects.",
        1.0: "Fully met across the relevant artifact with anchored evidence.",
    }
)

# Every criterion from evals/output_quality_rubric.md is retained here. The
# crosswalk makes future edits auditable rather than silently dropping a gate.
LEGACY_CRITERIA_CROSSWALK: Mapping[int, tuple[str, ...]] = MappingProxyType(
    {
        1: ("visibility.primary_visuals_render",),
        2: ("trustworthiness.calculations_reconciled", "trustworthiness.claims_trace_to_evidence"),
        3: ("executive_suitability.answer_first", "readability.logical_sequence"),
        4: ("readability.logical_sequence",),
        5: ("executive_suitability.answer_first",),
        6: ("executive_suitability.decision_relevant", "readability.scan_friendly_density"),
        7: ("trustworthiness.claims_trace_to_evidence", "readability.concise_prose"),
        8: ("executive_suitability.action_owner_timing", "executive_suitability.decision_relevant"),
        9: ("gate.critical_workflow_free",),
        10: ("gate.artifacts_openable", "trustworthiness.artifact_integrity_receipts"),
        11: ("gate.unsupported_claims_absent", "trustworthiness.claims_trace_to_evidence"),
        12: ("trustworthiness.calculations_reconciled", "trustworthiness.sources_disclosed"),
        13: ("trustworthiness.limitations_and_uncertainty", "gate.calculations_valid"),
        14: ("executive_suitability.action_owner_timing", "gate.unsupported_claims_absent"),
        15: ("gate.high_stakes_safety_clear", "gate.sensitive_data_protected"),
        16: ("gate.evidence_consistent",),
        17: ("readability.clear_information_hierarchy", "visibility.layout_fits_medium", "gate.artifact_qa_passed"),
        18: ("visibility.no_overlap_or_clipping", "visibility.legible_text", "gate.artifact_qa_passed"),
    }
)


ANTI_GAMING_PRINCIPLES = (
    "Score observable behavior and rendered artifacts, not the presence of preferred keywords.",
    "Require evidence locators for positive observations; unsupported self-ratings receive at most half credit.",
    "Compute metrics from source artifacts where possible and never accept an agent's claimed score as proof.",
    "Keep benchmark fixtures hidden from runtime prompts and include counterexamples that contain rubric keywords.",
    "Use fixed datasets, seeds, tolerances, versions, and immutable benchmark IDs for comparisons.",
    "Fail closed on missing hard-gate evidence; a high average never cancels a safety or trust failure.",
    "Report every criterion and diagnostic so selective omission cannot improve the score.",
    "Compare quality and regression deltas; do not optimize output quality by breaking route isolation or reliability.",
    "Use held-out corruptions and metamorphic variants so record order or rubric vocabulary cannot change the verdict.",
)


def hard_gates_for_route(route: str) -> tuple[str, ...]:
    """Return the universal and medium-specific non-compensable gates."""

    try:
        return ROUTE_HARD_GATE_PROFILES[str(route)]
    except KeyError as exc:
        raise ValueError("route must be 'analytics_report' or 'html_dashboard'") from exc


def score_output_quality(
    observations: Mapping[str, Any],
    gate_results: Mapping[str, Any],
    *,
    required_gates: Sequence[str] = REQUIRED_HARD_GATES,
) -> QualityScore:
    """Return deterministic 0-100 scores and a fail-closed pass decision.

    Observation keys use either ``signal`` or ``dimension.signal``. Values can
    be a number in [0, 1], a boolean, an :class:`Observation`, or a mapping with
    ``value``, ``evidence``, ``provenance`` and an optional ``diagnostic``. Evidence strings
    should identify a file, receipt, selector, page, slide, or calculation.
    Provenance names the deterministic check or the accountable human rater.
    """

    dimension_scores: dict[str, DimensionScore] = {}
    all_diagnostics: list[str] = []
    for dimension_name, rubric in OUTPUT_QUALITY_RUBRICS.items():
        earned = 0.0
        criterion_results: list[Mapping[str, Any]] = []
        diagnostics: list[str] = []
        for criterion in rubric.criteria:
            raw = observations.get(f"{dimension_name}.{criterion.signal}", observations.get(criterion.signal))
            observation = _coerce_observation(raw)
            effective_value = observation.value
            if effective_value > 0 and not observation.evidence:
                effective_value = min(effective_value, 0.5)
                diagnostics.append(f"{dimension_name}.{criterion.signal}: positive score lacks evidence and was capped at 50%.")
            if effective_value > 0 and not observation.provenance:
                effective_value = min(effective_value, 0.5)
                diagnostics.append(f"{dimension_name}.{criterion.signal}: positive score lacks check/rater provenance and was capped at 50%.")
            if effective_value < 1:
                diagnostics.append(observation.diagnostic or f"{dimension_name}.{criterion.signal}: {criterion.diagnostic}")
            points = criterion.weight * effective_value
            earned += points
            criterion_results.append(
                MappingProxyType(
                    {
                        "signal": criterion.signal,
                        "label": criterion.label,
                        "weight": criterion.weight,
                        "raw_value": observation.value,
                        "effective_value": effective_value,
                        "points": round(points, 2),
                        "evidence": list(observation.evidence),
                        "provenance": observation.provenance,
                    }
                )
            )
        score = round(earned, 1)
        dimension_scores[dimension_name] = DimensionScore(
            name=dimension_name,
            score=score,
            minimum_score=rubric.minimum_score,
            passed=score >= rubric.minimum_score,
            criteria=tuple(criterion_results),
            diagnostics=tuple(_deduplicate(diagnostics)),
        )
        all_diagnostics.extend(diagnostics)

    overall = round(
        sum(dimension_scores[name].score * rubric.weight for name, rubric in OUTPUT_QUALITY_RUBRICS.items()) / 100.0,
        1,
    )
    normalized_gates = {name: gate_results.get(name) is True for name in required_gates}
    failed_gates = tuple(name for name, passed in normalized_gates.items() if not passed)
    for gate in failed_gates:
        if gate not in gate_results:
            all_diagnostics.append(f"hard_gate.{gate}: result missing; failed closed.")
        else:
            all_diagnostics.append(f"hard_gate.{gate}: failed.")
    dimension_failures = [name for name, result in dimension_scores.items() if not result.passed]
    if dimension_failures:
        all_diagnostics.append(f"dimension minimum not met: {', '.join(dimension_failures)}.")
    if overall < OVERALL_MINIMUM_SCORE:
        all_diagnostics.append(f"overall score {overall:.1f} is below {OVERALL_MINIMUM_SCORE:.1f}.")
    passed = not failed_gates and not dimension_failures and overall >= OVERALL_MINIMUM_SCORE
    return QualityScore(
        rubric_version=OUTPUT_QUALITY_RUBRIC_VERSION,
        dimensions=MappingProxyType(dimension_scores),
        overall_score=overall,
        overall_minimum=OVERALL_MINIMUM_SCORE,
        hard_gates=MappingProxyType(normalized_gates),
        failed_gates=failed_gates,
        diagnostics=tuple(_deduplicate(all_diagnostics)),
        passed=passed,
    )


def _coerce_observation(raw: Any) -> Observation:
    if isinstance(raw, Observation):
        return Observation(
            _bounded(raw.value),
            tuple(str(item) for item in raw.evidence if str(item).strip()),
            str(raw.provenance).strip(),
            raw.diagnostic,
        )
    if isinstance(raw, Mapping):
        evidence_raw = raw.get("evidence", ())
        if isinstance(evidence_raw, str):
            evidence = (evidence_raw,) if evidence_raw.strip() else ()
        elif isinstance(evidence_raw, Sequence):
            evidence = tuple(str(item) for item in evidence_raw if str(item).strip())
        else:
            evidence = ()
        return Observation(
            value=_bounded(raw.get("value", 0)),
            evidence=evidence,
            provenance=str(raw.get("provenance", "")).strip(),
            diagnostic=str(raw.get("diagnostic", "")).strip(),
        )
    return Observation(value=_bounded(raw))


def _bounded(value: Any) -> float:
    if value is True:
        return 1.0
    if value is False or value is None:
        return 0.0
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    bounded = min(1.0, max(0.0, numeric))
    # Round down to the nearest explicit anchor so ambiguous values cannot
    # inflate a score. Callers must choose one of five observable levels.
    return max(anchor for anchor in OBSERVATION_ANCHORS if anchor <= bounded)


def _deduplicate(items: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))
