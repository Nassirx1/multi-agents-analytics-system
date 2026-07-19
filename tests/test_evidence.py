import unittest

import pandas as pd

from analytics_workflow.evidence import (
    artifact_dependency_hash,
    build_evidence_bundle,
    validate_and_sanitize_workflow,
)


class EvidenceContractTests(unittest.TestCase):
    def test_correlation_superlative_is_recomputed_from_attached_rows(self) -> None:
        state = {
            "analysis_results": {
                "analysis_artifacts": [
                    {
                        "artifact_id": "correlations",
                        "chart_type": "heatmap",
                        "finding": "Stress has the strongest correlation with outcome.",
                        "data": [
                            {"variable": "stress", "outcome": 0.17},
                            {"variable": "sleep_hours", "outcome": -0.31},
                            {"variable": "outcome", "outcome": 1.0},
                        ],
                    },
                    {
                        "artifact_id": "decision_tree_rules",
                        "chart_type": "decision_tree",
                        "data": {"target": "outcome", "nodes": [], "edges": []},
                    },
                ],
                "figure_captions": {},
            },
            "agent_outputs": {},
            "csv_data": {},
            "run_manifest": {"datasets": []},
        }

        bundle = build_evidence_bundle(state)

        claim = next(item["claim"] for item in bundle["evidence"] if item["evidence_id"] == "correlations")
        self.assertIn("sleep hours", claim.lower())
        self.assertIn("r=-0.31", claim)

    def test_high_stakes_tree_thresholds_are_removed_and_evidence_is_attached(self) -> None:
        frame = pd.DataFrame(
            {
                "stress_level": [2, 9, 8, 3],
                "sleep_hours": [8, 4, 5, 7],
                "social_interaction_level": ["high", "low", "medium", "high"],
                "depression_label": [0, 1, 1, 0],
            }
        )
        state = {
            "user_data_description": "Teen mental health analysis for depression support.",
            "decision_tree_target_column": "depression_label",
            "csv_data": {"teen.csv": frame},
            "run_manifest": {"datasets": [{"name": "teen.csv", "rows": 4, "columns": 4}]},
            "analysis_results": {
                "analysis_artifacts": [
                    {
                        "artifact_id": "decision_tree_rules",
                        "chart_type": "decision_tree",
                        "finding": "Exploratory tree rules.",
                        "data": {
                            "target": "depression_label",
                            "test_accuracy": "75%",
                            "baseline_accuracy": "50%",
                            "nodes": [
                                {"id": "root", "type": "split", "feature": "stress_level", "threshold": "6", "threshold_unit": "original"},
                                {"id": "leaf", "type": "leaf", "prediction": "Yes"},
                            ],
                            "edges": [{"source": "root", "target": "leaf", "label": "True"}],
                        },
                    }
                ]
            },
            "agent_outputs": {
                "market_researcher": {"sources_cited": []},
                "business_translator": {},
                "decision_maker": {
                    "recommendations": [
                        {
                            "rank": 1,
                            "action": "Use tree rules stress_level >= 8 and social_interaction_level = low.",
                            "evidence": "Decision tree rule",
                        }
                    ]
                },
            },
        }

        receipt = validate_and_sanitize_workflow(state)
        recommendation = state["agent_outputs"]["decision_maker"]["recommendations"][0]

        self.assertEqual(recommendation["decision_status"], "human_review_required")
        self.assertIn("decision_tree_rules", recommendation["evidence_ids"])
        self.assertNotIn(">= 8", recommendation["action"])
        self.assertTrue(any(item["code"] == "unsupported_model_claim_replaced" for item in receipt["issues"]))

    def test_dependency_hash_ignores_validation_timestamp(self) -> None:
        state = {
            "evidence_bundle": {"bundle_hash": "abc"},
            "agent_outputs": {"decision_maker": {}, "business_translator": {}},
            "quality_receipt": {"status": "ready_to_share", "validated_at": "first"},
        }
        first = artifact_dependency_hash(state, "pdf")
        state["quality_receipt"]["validated_at"] = "second"
        second = artifact_dependency_hash(state, "pdf")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
