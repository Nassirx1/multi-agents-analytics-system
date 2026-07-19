from __future__ import annotations

import dataclasses
import json
import re
import unittest
from pathlib import Path

from analytics_workflow.workflow_steps import ANALYTICS_REPORT_STEPS, HTML_DASHBOARD_STEPS
from benchmark_suite.catalog import (
    BENCHMARK_CATALOG_VERSION,
    BenchmarkCase,
    benchmark_catalog_manifest,
    load_benchmark_catalog,
    validate_benchmark_catalog,
)
from benchmark_suite.rubrics import (
    ANTI_GAMING_PRINCIPLES,
    LEGACY_CRITERIA_CROSSWALK,
    OUTPUT_QUALITY_DIMENSIONS,
    OUTPUT_QUALITY_RUBRICS,
    REQUIRED_HARD_GATES,
    hard_gates_for_route,
    score_output_quality,
)


def _perfect_observations() -> dict[str, dict[str, object]]:
    return {
        f"{dimension}.{criterion.signal}": {
            "value": 1,
            "evidence": [f"receipt.json#/{dimension}/{criterion.signal}"],
            "provenance": "deterministic:test_probe",
        }
        for dimension, rubric in OUTPUT_QUALITY_RUBRICS.items()
        for criterion in rubric.criteria
    }


def _passing_gates(route: str | None = None) -> dict[str, bool]:
    gates = hard_gates_for_route(route) if route else REQUIRED_HARD_GATES
    return {gate: True for gate in gates}


class BenchmarkCatalogTests(unittest.TestCase):
    def test_catalog_is_valid_broad_and_credential_free(self) -> None:
        cases = load_benchmark_catalog()
        self.assertGreaterEqual(len(cases), 24)
        self.assertEqual(validate_benchmark_catalog(cases), [])
        self.assertTrue(all(case.deterministic and not case.credentials_required for case in cases))
        self.assertEqual(BENCHMARK_CATALOG_VERSION, "1.0.0")

    def test_ids_are_unique_versioned_and_case_contract_is_frozen(self) -> None:
        cases = load_benchmark_catalog()
        ids = [case.id for case in cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(re.fullmatch(r"maa\.[a-z0-9_]+\.[a-z0-9_]+\.v[1-9][0-9]*", case_id) for case_id in ids))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            cases[0].name = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            cases[0].expected["changed"] = True  # type: ignore[index]
        self.assertEqual(cases[0].check, cases[0].target)

    def test_every_workflow_stage_has_direct_benchmark_coverage(self) -> None:
        covered = {stage for case in load_benchmark_catalog() for stage in case.stages}
        self.assertTrue(set(ANALYTICS_REPORT_STEPS).issubset(covered))
        self.assertTrue(set(HTML_DASHBOARD_STEPS).issubset(covered))

    def test_catalog_covers_routes_agents_generators_reliability_and_quality(self) -> None:
        cases = load_benchmark_catalog()
        self.assertTrue({"routing", "agent", "generator", "workflow", "reliability", "quality"}.issubset({case.category for case in cases}))
        routes = {route for case in cases for route in case.routes}
        self.assertTrue({"analytics_report", "html_dashboard", "shared"}.issubset(routes))
        targets = {case.target for case in cases}
        self.assertIn("quality.held_out_metamorphic_controls", targets)
        self.assertIn("workflow.analytics_report_e2e", targets)
        self.assertIn("workflow.html_dashboard_e2e", targets)

    def test_manifest_is_canonical_json_and_hash_is_stable(self) -> None:
        first = benchmark_catalog_manifest()
        second = benchmark_catalog_manifest()
        self.assertEqual(first, second)
        self.assertEqual(first["benchmark_count"], len(load_benchmark_catalog()))
        self.assertRegex(first["catalog_sha256"], r"^[0-9a-f]{64}$")
        json.dumps(first, sort_keys=True)

    def test_readable_catalog_receipt_matches_executable_source(self) -> None:
        receipt_path = Path(__file__).resolve().parents[1] / "evals" / "benchmark_catalog.v1.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        manifest = benchmark_catalog_manifest()
        self.assertEqual(receipt["catalog_version"], manifest["catalog_version"])
        self.assertEqual(receipt["catalog_sha256"], manifest["catalog_sha256"])
        self.assertEqual(receipt["benchmark_count"], manifest["benchmark_count"])
        self.assertEqual(
            [(item["id"], item["target"]) for item in receipt["benchmarks"]],
            [(item["id"], item["target"]) for item in manifest["benchmarks"]],
        )

    def test_validator_rejects_unversioned_duplicate_and_live_case(self) -> None:
        original = load_benchmark_catalog()[0]
        invalid = BenchmarkCase(
            id="bad-id",
            name="invalid",
            category="routing",
            target="",
            routes=("unknown",),
            stages=(),
            dimensions=("unknown",),
            timeout_seconds=0,
            fixture={},
            expected={},
            deterministic=False,
            credentials_required=True,
        )
        errors = validate_benchmark_catalog((original, original, invalid))
        text = "\n".join(errors)
        self.assertIn("duplicate id", text)
        self.assertIn("versioned immutable ID", text)
        self.assertIn("deterministic and credential-free", text)


class OutputQualityRubricTests(unittest.TestCase):
    def test_dimension_and_criterion_weights_are_normalized(self) -> None:
        self.assertEqual(tuple(OUTPUT_QUALITY_RUBRICS), OUTPUT_QUALITY_DIMENSIONS)
        self.assertEqual(sum(rubric.weight for rubric in OUTPUT_QUALITY_RUBRICS.values()), 100)
        for rubric in OUTPUT_QUALITY_RUBRICS.values():
            self.assertEqual(sum(criterion.weight for criterion in rubric.criteria), 100)

    def test_perfect_evidenced_output_scores_100_and_passes(self) -> None:
        result = score_output_quality(_perfect_observations(), _passing_gates())
        self.assertTrue(result.passed)
        self.assertEqual(result.overall_score, 100.0)
        self.assertTrue(all(score.score == 100.0 for score in result.dimensions.values()))
        json.dumps(result.to_dict(), sort_keys=True)

    def test_positive_claim_without_evidence_is_capped_and_diagnosed(self) -> None:
        observations = {key: 1 for key in _perfect_observations()}
        result = score_output_quality(observations, _passing_gates())
        self.assertEqual(result.overall_score, 50.0)
        self.assertFalse(result.passed)
        self.assertTrue(any("lacks evidence" in item for item in result.diagnostics))

    def test_positive_claim_without_provenance_is_capped_and_diagnosed(self) -> None:
        observations = {
            key: {"value": 1, "evidence": ["artifact#anchor"]}
            for key in _perfect_observations()
        }
        result = score_output_quality(observations, _passing_gates())
        self.assertEqual(result.overall_score, 50.0)
        self.assertTrue(any("lacks check/rater provenance" in item for item in result.diagnostics))

    def test_hard_gate_is_noncompensable(self) -> None:
        gates = _passing_gates()
        gates["unsupported_claims_absent"] = False
        result = score_output_quality(_perfect_observations(), gates)
        self.assertEqual(result.overall_score, 100.0)
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_gates, ("unsupported_claims_absent",))

    def test_missing_hard_gate_fails_closed(self) -> None:
        result = score_output_quality(_perfect_observations(), {})
        self.assertFalse(result.passed)
        self.assertEqual(set(result.failed_gates), set(REQUIRED_HARD_GATES))
        self.assertTrue(any("result missing" in item for item in result.diagnostics))

    def test_dimension_floor_is_noncompensable_even_when_overall_passes(self) -> None:
        observations = _perfect_observations()
        for criterion in OUTPUT_QUALITY_RUBRICS["readability"].criteria:
            observations[f"readability.{criterion.signal}"] = {
                "value": 0.5,
                "evidence": ["held-out#readability-defect"],
                "provenance": "deterministic:negative_control",
            }
        result = score_output_quality(observations, _passing_gates())
        self.assertGreaterEqual(result.overall_score, result.overall_minimum)
        self.assertFalse(result.dimensions["readability"].passed)
        self.assertFalse(result.passed)

    def test_values_are_bounded_and_diagnostics_are_readable(self) -> None:
        observations = _perfect_observations()
        key = next(iter(observations))
        observations[key] = {"value": 99, "evidence": ["receipt#anchor"]}
        observations[key]["provenance"] = "deterministic:test_probe"
        result = score_output_quality(observations, _passing_gates())
        dimension, signal = key.split(".", 1)
        item = next(item for item in result.dimensions[dimension].criteria if item["signal"] == signal)
        self.assertEqual(item["effective_value"], 1.0)
        self.assertIsInstance(result.to_dict()["diagnostics"], list)

    def test_route_profiles_preserve_report_and_html_specific_gates(self) -> None:
        report = hard_gates_for_route("analytics_report")
        dashboard = hard_gates_for_route("html_dashboard")
        self.assertIn("report_slide_evidence_hash_match", report)
        self.assertIn("pdf_semantic_qa_passed", report)
        self.assertIn("dashboard_runtime_absent", report)
        self.assertIn("html_self_contained", dashboard)
        self.assertIn("dashboard_interactions_functional", dashboard)
        self.assertIn("report_artifacts_absent", dashboard)
        with self.assertRaises(ValueError):
            hard_gates_for_route("power_bi")

    def test_all_legacy_output_criteria_have_explicit_crosswalk(self) -> None:
        self.assertEqual(set(LEGACY_CRITERIA_CROSSWALK), set(range(1, 19)))
        self.assertTrue(all(targets for targets in LEGACY_CRITERIA_CROSSWALK.values()))

    def test_anti_gaming_policy_requires_held_out_and_evidence_controls(self) -> None:
        policy = " ".join(ANTI_GAMING_PRINCIPLES).lower()
        self.assertIn("held-out", policy)
        self.assertIn("evidence", policy)
        self.assertIn("keywords", policy)


if __name__ == "__main__":
    unittest.main()
