import tempfile
import unittest
from pathlib import Path

from analytics_workflow.run_checkpoints import load_run_checkpoint
from scripts.export_agent_execution_logs import build_execution_logs


class RunCheckpointTests(unittest.TestCase):
    def test_execution_log_export_matches_agent_evaluator_contract(self) -> None:
        payload = build_execution_logs(
            {
                "run_id": "run-1",
                "step_metrics": [
                    {
                        "step": 1,
                        "name": "Data Understander",
                        "status": "done",
                        "started_at": "2026-01-01T00:00:00Z",
                        "ended_at": "2026-01-01T00:00:02Z",
                        "duration_ms": 2000,
                        "prompt_tokens": 100,
                        "completion_tokens": 25,
                        "estimated_cost_usd": 0.01,
                    }
                ],
            }
        )
        entry = payload["execution_logs"][0]
        self.assertEqual(entry["status"], "success")
        self.assertEqual(entry["tokens_used"]["total_tokens"], 125)
        self.assertEqual(entry["agent_id"], "data_understander")

    def test_checkpoint_loader_rejects_missing_required_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "run_manifest.json"):
                load_run_checkpoint(Path(temp_dir))


if __name__ == "__main__":
    unittest.main()
