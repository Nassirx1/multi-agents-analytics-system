from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmark_suite.runner import run_default_catalog, verify_frozen_catalog_manifest
from benchmark_suite.rubrics import OVERALL_MINIMUM_SCORE


QUALITY_ID = "maa.quality.four_dimension_output_quality.v1"
METAMORPHIC_ID = "maa.quality.held_out_metamorphic_controls.v1"


class OptimizerBFinalAcceptanceTests(unittest.TestCase):
    def test_final_verdict_accepts_harness_but_preserves_workflow_failure(self) -> None:
        path = Path(__file__).resolve().parents[1] / "evals" / "recursive_benchmark_loop" / "optimizer_b_final.json"
        review = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(review["verdict"], "benchmark_assurance_accepted_workflow_candidate_rejected")
        self.assertEqual(review["verification"]["focused_tests"]["result"], "54 passed")
        self.assertFalse(review["verification"]["full_pilot"]["suite_passed"])
        self.assertEqual(review["verification"]["full_pilot"]["result"], "29 passed, 1 failed, 0 skipped")
        self.assertEqual(review["release_gate"]["benchmark_infrastructure"], "accepted")
        self.assertIn("rejected", review["release_gate"]["analytics_workflow_candidate"])

    def test_default_quality_path_uses_assured_scoring_on_both_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            suite = run_default_catalog(output_dir=Path(tmp), benchmark_ids=(QUALITY_ID,))
        quality = [item for item in suite.results if item.benchmark_id == QUALITY_ID]
        self.assertEqual({item.route for item in quality}, {"analytics_report", "html_dashboard"})
        self.assertTrue(all(item.status == "pass" for item in quality), [item.diagnostics for item in quality])
        self.assertTrue(all(item.overall_score is not None and item.overall_score >= 85 for item in quality))
        self.assertRegex(suite.result_fingerprint, r"^[0-9a-f]{64}$")

    def test_raw_boolean_quality_executor_cannot_bypass_default_runner(self) -> None:
        def fabricated(context):
            return {
                "passed": True,
                "scores": {dimension: 100 for dimension in context.dimensions},
                "overall_score": 100,
                "gate_results": {gate: True for gate in context.hard_gates},
            }

        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(
            ValueError, "assured evaluator override is forbidden"
        ):
            run_default_catalog(
                output_dir=Path(tmp),
                benchmark_ids=(QUALITY_ID,),
                executors={"quality.four_dimension_score": fabricated},
            )

    def test_executable_negative_controls_run_through_default_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            suite = run_default_catalog(output_dir=Path(tmp), benchmark_ids=(METAMORPHIC_ID,))
        controls = [item for item in suite.results if item.benchmark_id == METAMORPHIC_ID]
        self.assertEqual(len(controls), 2)
        self.assertTrue(all(item.status == "pass" for item in controls), [item.diagnostics for item in controls])
        for item in controls:
            checks = item.metadata.get("checks", {})
            for name in (
                "keyword_injection_no_gain",
                "evidence_corruption_fails_gate",
                "blank_visual_fails_gate",
                "secret_leakage_fails",
                "wrong_route_artifact_fails",
            ):
                self.assertIs(checks.get(name), True, (item.route, name, checks))

    def test_catalog_fingerprint_tampering_aborts_before_execution(self) -> None:
        frozen = Path(__file__).resolve().parents[1] / "evals" / "benchmark_catalog.v1.json"
        manifest = json.loads(frozen.read_text(encoding="utf-8"))
        manifest["catalog_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "does not match the frozen receipt"):
            verify_frozen_catalog_manifest(manifest, frozen)

    def test_threshold_is_85_and_result_fingerprint_is_repeatable(self) -> None:
        self.assertEqual(OVERALL_MINIMUM_SCORE, 85.0)
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            one = run_default_catalog(output_dir=Path(first), benchmark_ids=(METAMORPHIC_ID,))
            two = run_default_catalog(output_dir=Path(second), benchmark_ids=(METAMORPHIC_ID,))
        self.assertEqual(one.result_fingerprint, two.result_fingerprint)


if __name__ == "__main__":
    unittest.main()
