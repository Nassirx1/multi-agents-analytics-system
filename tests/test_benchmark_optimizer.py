from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROPOSAL_PATH = ROOT / "evals" / "recursive_benchmark_loop" / "optimizer_a_proposal.json"
LEGACY_RUBRIC_PATH = ROOT / "evals" / "output_quality_rubric.md"
TRUST_AUDIT_PATH = ROOT / "scripts" / "audit_run_trust.py"


class RecursiveOptimizerAProposalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.proposal = json.loads(PROPOSAL_PATH.read_text(encoding="utf-8"))

    def test_proposal_is_bounded_and_does_not_claim_an_unrun_score(self) -> None:
        self.assertEqual(self.proposal["scope"], "benchmark_and_evaluation_infrastructure_only")
        self.assertTrue(self.proposal["invariants"]["forbid_production_runtime_edits"])
        self.assertLessEqual(self.proposal["invariants"]["max_recursive_iterations"], 3)
        self.assertIsNone(self.proposal["baseline"]["execution_score"])
        self.assertEqual(
            self.proposal["baseline"]["execution_score_status"],
            "pending_workflow_evaluator_receipt",
        )

    def test_every_change_has_impact_risk_and_regression_gate(self) -> None:
        changes = self.proposal["proposed_changes"]
        self.assertGreaterEqual(len(changes), 5)
        self.assertEqual(len({change["id"] for change in changes}), len(changes))
        for change in changes:
            with self.subTest(change=change["id"]):
                for field in ("change", "expected_impact", "risk", "regression_gate"):
                    self.assertGreater(len(change[field].strip()), 20)

    def test_proposal_closes_html_and_anti_gaming_gaps(self) -> None:
        text = json.dumps(self.proposal, sort_keys=True).lower()
        for required in (
            "html",
            "route",
            "held-out",
            "negative control",
            "metamorphic",
            "unsupported claim",
            "blank",
            "clipped",
            "secret",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_proposal_cannot_weaken_current_normalized_threshold(self) -> None:
        legacy_normalized_threshold = 76 / 90
        proposed_floor = self.proposal["invariants"]["minimum_normalized_pass_threshold"]
        self.assertGreaterEqual(proposed_floor, 0.84)
        self.assertLessEqual(proposed_floor, legacy_normalized_threshold)

    def test_legacy_rubric_trust_gates_are_preserved_as_invariants(self) -> None:
        rubric = LEGACY_RUBRIC_PATH.read_text(encoding="utf-8").lower()
        invariants = " ".join(self.proposal["invariants"]["preserve_legacy_critical_gates"]).lower()
        for phrase in (
            "critical workflow failure",
            "unsupported claim",
            "high-stakes policy blocker",
            "evidence hash",
            "semantic qa",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, rubric)
                self.assertIn(phrase, invariants)

    def test_existing_trust_audit_controls_remain_explicit(self) -> None:
        audit = TRUST_AUDIT_PATH.read_text(encoding="utf-8")
        controls = " ".join(self.proposal["invariants"]["preserve_trust_audit_controls"])
        for code_term in ("pdf.get(\"valid\")", "slides.valid", "hash_match", "delivery_complete"):
            self.assertIn(code_term, audit)
        for human_term in ("pdf validity", "slide validity", "evidence hash equality", "complete delivery bundle"):
            self.assertIn(human_term, controls)


if __name__ == "__main__":
    unittest.main()
