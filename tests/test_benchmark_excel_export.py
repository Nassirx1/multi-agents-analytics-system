from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.export_benchmark_results_excel import export_benchmark_workbook


class BenchmarkExcelExportTests(unittest.TestCase):
    def test_workbook_contains_results_and_improvement_sheets(self) -> None:
        receipt = {
            "passed": False,
            "counts": {"pass": 1, "fail": 1, "skip": 0},
            "catalog_case_count": 2,
            "execution_count": 2,
            "result_fingerprint": "abc123",
            "dimension_summary": {"trustworthiness": {"count": 2, "mean": 80, "minimum": 60, "maximum": 100}},
            "catalog_manifest": {
                "catalog_sha256": "catalog123",
                "benchmarks": [
                    {
                        "id": "maa.agent.actionable_bounded_recommendations.v1",
                        "name": "Decision contract",
                        "category": "agent",
                        "routes": ["analytics_report"],
                        "stages": ["Decision Maker"],
                        "dimensions": ["trustworthiness"],
                        "hard_gates": ["unsupported_claims_absent"],
                        "tags": ["decision"],
                        "target": "agent.decision_maker",
                        "expected": {"metric": "required"},
                    }
                ],
            },
            "results": [
                {
                    "benchmark_id": "maa.agent.actionable_bounded_recommendations.v1",
                    "name": "Decision contract",
                    "category": "agent",
                    "route": "analytics_report",
                    "target": "agent.decision_maker",
                    "stages": ["Decision Maker"],
                    "status": "fail",
                    "duration_ms": 1.25,
                    "overall_score": 80,
                    "scores": {"trustworthiness": 80},
                    "failure_kind": "assertion",
                    "diagnostics": ["required_action_fields: deterministic assertion failed"],
                    "skipped_reason": "",
                    "artifacts": [],
                },
                {
                    "benchmark_id": "maa.routing.choice_before_credentials.v1",
                    "name": "Routing",
                    "category": "routing",
                    "route": "shared",
                    "target": "routing.initial_choice_before_credentials",
                    "stages": [],
                    "status": "pass",
                    "duration_ms": 0.5,
                    "scores": {},
                    "diagnostics": [],
                    "artifacts": [],
                },
            ],
        }
        optimizer = {
            "finding_disposition": [
                {"id": "B12_RENDERED_VISIBILITY_COVERAGE_IS_TOO_NARROW", "status": "open", "reason": "Coverage gap"}
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt_path = root / "benchmark_results.json"
            optimizer_path = root / "optimizer.json"
            output_path = root / "benchmark_results.xlsx"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            optimizer_path.write_text(json.dumps(optimizer), encoding="utf-8")
            export_benchmark_workbook(receipt_path, output_path, optimizer_review_path=optimizer_path)
            self.assertTrue(output_path.is_file())
            self.assertGreater(output_path.stat().st_size, 5000)
            with zipfile.ZipFile(output_path) as workbook:
                workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8")
                shared_strings = workbook.read("xl/sharedStrings.xml").decode("utf-8")
        for sheet_name in ("Summary", "Benchmark Results", "Improvement Plan", "Catalog Coverage"):
            self.assertIn(sheet_name, workbook_xml)
        self.assertIn("actionable_bounded_recommendations", shared_strings)
        self.assertIn("required_action_fields", shared_strings)
        self.assertIn("B12_RENDERED_VISIBILITY", shared_strings)


if __name__ == "__main__":
    unittest.main()
