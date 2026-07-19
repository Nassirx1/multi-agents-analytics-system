from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOOP_DIR = ROOT / "evals" / "recursive_benchmark_loop"
CRITIQUE_PATH = LOOP_DIR / "optimizer_b_critique.json"
REVIEW_PATH = LOOP_DIR / "optimizer_b_review.md"
A_PROPOSAL_PATH = LOOP_DIR / "optimizer_a_proposal.json"
A_TEST_PATH = ROOT / "tests" / "test_benchmark_optimizer.py"


class RecursiveOptimizerBAdversarialReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.critique = json.loads(CRITIQUE_PATH.read_text(encoding="utf-8"))
        cls.proposal = json.loads(A_PROPOSAL_PATH.read_text(encoding="utf-8"))

    def test_pass_one_is_rejected_without_redefining_scope(self) -> None:
        self.assertEqual(self.critique["reviews_proposal_id"], "optimizer-a-pass-01")
        self.assertEqual(self.critique["verdict"], "reject_pending_round_2")
        self.assertEqual(self.critique["scope"], "benchmark_and_evaluation_infrastructure_only")
        self.assertFalse(self.critique["preserved_invariants"]["production_runtime_edits_authorized"])
        self.assertFalse(self.critique["handoff"]["accept_pass_1"])
        self.assertEqual(self.critique["handoff"]["next_round"], 2)

    def test_historical_inputs_are_content_addressed(self) -> None:
        snapshot = self.critique["snapshot"]
        # The pass-1 proposal is the historical review input. Its companion
        # test was intentionally strengthened in round 2 and is not immutable.
        digest = hashlib.sha256(A_PROPOSAL_PATH.read_bytes()).hexdigest()
        self.assertEqual(digest, snapshot["optimizer_a_proposal_sha256"])

    def test_critique_covers_every_requested_adversarial_risk(self) -> None:
        findings = self.critique["findings"]
        self.assertGreaterEqual(len(findings), 15)
        categories = {finding["category"] for finding in findings}
        required = {
            "benchmark_gaming",
            "circular_evaluation",
            "unverifiable_subjectivity",
            "flakiness",
            "weak_held_out_tests",
            "threshold_inflation",
            "iteration_comparability",
            "recursive_acceptance",
        }
        self.assertTrue(required.issubset(categories), required - categories)
        self.assertEqual(len({finding["id"] for finding in findings}), len(findings))
        for finding in findings:
            with self.subTest(finding=finding["id"]):
                self.assertIn(finding["severity"], {"blocker", "high", "medium", "low"})
                self.assertTrue(finding["evidence"])
                for field in ("observation", "exploit", "impact", "required_fix", "acceptance_test"):
                    self.assertGreaterEqual(len(finding[field].strip()), 25)

    def test_counterexamples_are_recorded_as_observed_not_hypothetical(self) -> None:
        probe = self.critique["demonstrations"]["fabricated_evidence_probe"]
        self.assertEqual(probe["result"], "passed")
        self.assertEqual(probe["overall_score"], 100.0)
        self.assertEqual(set(probe["dimension_scores"]), {
            "readability", "visibility", "trustworthiness", "executive_suitability"
        })
        self.assertTrue(all(score == 100.0 for score in probe["dimension_scores"].values()))
        self.assertIn("without an artifact", probe["interpretation"])

    def test_threshold_review_preserves_the_stricter_legacy_boundary(self) -> None:
        legacy = 76 / 90
        required = self.critique["preserved_invariants"]["minimum_analytics_report_normalized_threshold"]
        probe = self.critique["demonstrations"]["threshold_probe"]
        self.assertAlmostEqual(required, legacy)
        self.assertAlmostEqual(probe["legacy_analytics_report_threshold"], legacy)
        self.assertLess(self.proposal["invariants"]["minimum_normalized_pass_threshold"], legacy)
        self.assertLess(probe["implemented_overall_threshold"], legacy)

    def test_no_existing_hard_gate_is_weakened_or_omitted(self) -> None:
        preserved = set(self.critique["preserved_invariants"]["hard_gates"])
        expected = {
            "critical_workflow_free",
            "unsupported_claims_absent",
            "high_stakes_safety_clear",
            "evidence_consistent",
            "calculations_valid",
            "artifacts_openable",
            "artifact_qa_passed",
            "primary_visuals_render",
            "sensitive_data_protected",
        }
        self.assertEqual(preserved, expected)
        legacy_text = " ".join(self.proposal["invariants"]["preserve_legacy_critical_gates"]).lower()
        for phrase in ("critical workflow failure", "unsupported claim", "high-stakes policy blocker", "evidence hash", "semantic qa"):
            self.assertIn(phrase, legacy_text)

    def test_round_two_actions_assign_enforceable_proof(self) -> None:
        actions = self.critique["round_2_required_actions"]
        self.assertGreaterEqual(len(actions), 10)
        self.assertEqual(len({action["id"] for action in actions}), len(actions))
        self.assertEqual(
            {action["owner"] for action in actions},
            {"optimizer_a", "benchmark_architect", "workflow_evaluator"},
        )
        joined = json.dumps(actions).lower()
        for term in (
            "threshold", "typed", "independent", "held-out", "registry", "anchor",
            "viewport", "stability", "immutable", "legacy",
        ):
            with self.subTest(term=term):
                self.assertIn(term, joined)
        for action in actions:
            self.assertGreaterEqual(len(action["proof"].strip()), 20)

    def test_human_review_matches_canonical_critique_decision(self) -> None:
        review = REVIEW_PATH.read_text(encoding="utf-8").lower()
        self.assertIn("reject pass 1", review)
        self.assertIn("100/100", review)
        self.assertIn("76/90", review)
        self.assertIn("15 findings", review)
        self.assertIn("does not authorize analytics runtime changes", review)


if __name__ == "__main__":
    unittest.main()
