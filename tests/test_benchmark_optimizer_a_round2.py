from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from benchmark_suite.assurance import (
    ASSURANCE_VERSION,
    INDEPENDENT_GATE_EVALUATORS,
    build_artifact_inventory,
    score_output_quality_assured,
)
from benchmark_suite.rubrics import (
    OUTPUT_QUALITY_RUBRICS,
    OVERALL_MINIMUM_SCORE,
    hard_gates_for_route,
)


class AssuredScoringNegativeControlTests(unittest.TestCase):
    def _fixture(self, root: Path):
        signals = {
            criterion.signal: {"signal": criterion.signal, "passed": True}
            for rubric in OUTPUT_QUALITY_RUBRICS.values()
            for criterion in rubric.criteria
        }
        receipt_path = root / "independent_qa.json"
        receipt_path.write_text(json.dumps({"signals": signals}), encoding="utf-8")
        inventory = build_artifact_inventory(root)
        digest = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        observations = {
            f"{dimension}.{criterion.signal}": {
                "value": 1,
                "evidence": [{
                    "path": "independent_qa.json",
                    "sha256": digest,
                    "kind": "deterministic_check",
                    "selector": f"/signals/{criterion.signal}",
                    "signal": criterion.signal,
                }],
                "provenance": "deterministic:independent_qa",
            }
            for dimension, rubric in OUTPUT_QUALITY_RUBRICS.items()
            for criterion in rubric.criteria
        }
        gates = {
            gate: {
                "check_id": gate,
                "passed": True,
                "evaluator_id": INDEPENDENT_GATE_EVALUATORS[gate],
                "evaluator_version": ASSURANCE_VERSION,
                "artifact_sha256": digest,
                "observed_facts": {"verified": True},
                "evaluated_at": "2026-07-14T09:00:00Z",
                "route": "html_dashboard",
                "issuer_kind": "independent_deterministic",
            }
            for gate in hard_gates_for_route("html_dashboard")
        }
        return inventory, observations, gates

    def test_current_threshold_is_stricter_than_legacy_76_of_90(self) -> None:
        self.assertGreaterEqual(OVERALL_MINIMUM_SCORE / 100, 76 / 90)
        self.assertEqual(OVERALL_MINIMUM_SCORE, 85.0)

    def test_typed_hash_bound_evidence_and_independent_receipts_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inventory, observations, gates = self._fixture(Path(tmp))
            result = score_output_quality_assured(observations, gates, route="html_dashboard", inventory=inventory)
        self.assertTrue(result.passed, result.diagnostics)
        self.assertEqual(result.overall_score, 100.0)

    def test_fabricated_strings_and_raw_gate_booleans_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inventory, observations, gates = self._fixture(Path(tmp))
            for observation in observations.values():
                observation["evidence"] = ["not-a-real-locator"]
            result = score_output_quality_assured(
                observations, {gate: True for gate in gates}, route="html_dashboard", inventory=inventory
            )
        self.assertFalse(result.passed)
        self.assertEqual(result.overall_score, 0.0)
        text = " ".join(result.diagnostics)
        self.assertIn("typed evidence locator", text)
        self.assertIn("bare booleans are invalid", text)

    def test_stale_cross_run_and_wrong_signal_locators_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inventory, observations, gates = self._fixture(Path(tmp))
            keys = list(observations)
            observations[keys[0]]["evidence"][0]["sha256"] = "0" * 64
            observations[keys[1]]["evidence"][0]["path"] = "../outside.json"
            observations[keys[2]]["evidence"][0]["signal"] = "unrelated_signal"
            result = score_output_quality_assured(observations, gates, route="html_dashboard", inventory=inventory)
        self.assertFalse(result.passed)
        diagnostics = " ".join(result.diagnostics)
        self.assertIn("hash is stale", diagnostics)
        self.assertIn("escapes the run root", diagnostics)
        self.assertIn("does not match the scored criterion", diagnostics)

    def test_stale_or_self_asserted_gate_receipt_cannot_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inventory, observations, gates = self._fixture(Path(tmp))
            names = list(gates)
            gates[names[0]]["artifact_sha256"] = "f" * 64
            gates[names[1]]["issuer_kind"] = "agent_self_assertion"
            result = score_output_quality_assured(observations, gates, route="html_dashboard", inventory=inventory)
        self.assertFalse(result.passed)
        self.assertIn(names[0], result.failed_gates)
        self.assertIn(names[1], result.failed_gates)

    def test_inventory_rejects_absolute_and_parent_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "receipt.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "confined and relative"):
                build_artifact_inventory(root, ("../receipt.json",))
            with self.assertRaisesRegex(ValueError, "confined and relative"):
                build_artifact_inventory(root, (str((root / "receipt.json").resolve()),))


if __name__ == "__main__":
    unittest.main()
