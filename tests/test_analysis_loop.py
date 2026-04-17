import unittest
from unittest.mock import patch
import os
import tempfile

import analytics_workflow.pipeline_runtime as pipeline_runtime
from analytics_workflow.agents import DataScientistCoderAgent
from analytics_workflow.pipeline_runtime import MultiAgentOrchestrator
from analytics_workflow.runtime_config import build_runtime_config


class AnalysisLoopValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        config = build_runtime_config("openrouter-secret", "brave-secret")
        self.orchestrator = MultiAgentOrchestrator(config)

    def test_user_data_description_propagates_to_all_agents(self) -> None:
        description = "This dataset tracks telecom stock performance to support an investment decision."

        self.orchestrator.set_user_data_description(description)

        self.assertEqual(self.orchestrator.workflow_state["user_data_description"], description)
        for agent in self.orchestrator.agents.values():
            self.assertEqual(agent.context.get("user_data_description"), description)

    def test_detects_missing_visual_and_numeric_artifacts(self) -> None:
        execution = {
            "execution_status": "success",
            "figures_generated": ["figure_1.png"],
            "analysis_summary": {"headline": "Trend improved", "supporting_note": "Momentum looks better"},
            "business_findings": ["Some improvement observed."],
            "figure_captions": {},
        }

        issues = self.orchestrator._analysis_output_issues(execution)

        self.assertGreaterEqual(len(issues), 4)
        self.assertTrue(any("fewer than 3 saved figures" in issue for issue in issues))
        self.assertTrue(any("numeric evidence" in issue for issue in issues))
        self.assertTrue(any("business_findings" in issue for issue in issues))
        self.assertTrue(any("Missing figure captions" in issue for issue in issues))

    def test_accepts_visual_analysis_with_numeric_evidence(self) -> None:
        execution = {
            "execution_status": "success",
            "figures_generated": ["figure_1.png", "figure_2.png", "figure_3.png", "figure_4.png"],
            "analysis_summary": {
                "average_return_pct": 3.4,
                "peak_volume": "2.8M shares",
            },
            "business_findings": [
                "Higher trading volume aligns with stronger price momentum.",
                "Recent periods show more stable downside behavior.",
            ],
            "figure_captions": {
                "figure_1.png": "Price trend shows a positive closing pattern.",
                "figure_2.png": "Volume spikes coincide with larger moves.",
                "figure_3.png": "Returns remain concentrated around a narrow range.",
                "figure_4.png": "Rolling averages show improving momentum.",
            },
        }

        issues = self.orchestrator._analysis_output_issues(execution)

        self.assertEqual(issues, [])

    def test_coding_loop_stops_after_first_complete_success(self) -> None:
        class FakeCoder:
            def __init__(self) -> None:
                self.calls = 0
                self.context = {}

            def execute(self, analysis_plan, csv_data, iteration):
                self.calls += 1
                return "print('ready')"

        class FakeReviewer:
            def __init__(self) -> None:
                self.calls = 0

            def execute(self, *args, **kwargs):
                self.calls += 1
                return {"decision": "REVISE"}

        coder = FakeCoder()
        reviewer = FakeReviewer()
        self.orchestrator.agents["coder"] = coder
        self.orchestrator.agents["reviewer"] = reviewer
        self.orchestrator.workflow_state["csv_data"] = {"sample.csv": None}
        self.orchestrator._set_step = lambda *args, **kwargs: None
        self.orchestrator._execute_code = lambda code: {
            "execution_status": "success",
            "figures_generated": ["figure_1.png", "figure_2.png", "figure_3.png"],
            "analysis_summary": {"average_return_pct": 2.1, "peak_volume": "2.3M"},
            "business_findings": ["Trend improved with volume support.", "Risk remained bounded."],
            "figure_captions": {
                "figure_1.png": "Trend line improved.",
                "figure_2.png": "Volume spiked on key days.",
                "figure_3.png": "Momentum stayed positive.",
            },
        }

        result = self.orchestrator._coding_loop({"objectives": ["test"]}, max_iterations=4)

        self.assertEqual(result, "print('ready')")
        self.assertEqual(coder.calls, 1)
        self.assertEqual(reviewer.calls, 0)

    def test_coding_loop_stops_when_reviewer_approves_successful_code(self) -> None:
        class FakeCoder:
            def __init__(self) -> None:
                self.calls = 0
                self.context = {}

            def execute(self, analysis_plan, csv_data, iteration):
                self.calls += 1
                return "print('approved with warnings')"

        class FakeReviewer:
            def __init__(self) -> None:
                self.calls = 0

            def execute(self, *args, **kwargs):
                self.calls += 1
                return {"decision": "APPROVE"}

        coder = FakeCoder()
        reviewer = FakeReviewer()
        self.orchestrator.agents["coder"] = coder
        self.orchestrator.agents["reviewer"] = reviewer
        self.orchestrator.workflow_state["csv_data"] = {"sample.csv": None}
        self.orchestrator._set_step = lambda *args, **kwargs: None
        self.orchestrator._execute_code = lambda code: {
            "execution_status": "success",
            "figures_generated": ["figure_1.png"],
            "analysis_summary": {"headline": "Trend improved", "supporting_note": "Momentum looks better"},
            "business_findings": ["Some improvement observed."],
            "figure_captions": {},
        }

        result = self.orchestrator._coding_loop({"objectives": ["test"]}, max_iterations=4)

        self.assertEqual(result, "print('approved with warnings')")
        self.assertEqual(coder.calls, 1)
        self.assertEqual(reviewer.calls, 1)

    def test_execute_workflow_reuses_successful_loop_execution(self) -> None:
        self.orchestrator.workflow_state["csv_data"] = {"sample.csv": None}
        self.orchestrator._set_step = lambda *args, **kwargs: None

        self.orchestrator.agents["data_understander"] = type(
            "StubAgent",
            (),
            {"execute": lambda self, *args, **kwargs: {"executive_summary": "ok", "datasets": {}}},
        )()
        self.orchestrator.agents["market_researcher"] = type(
            "StubAgent",
            (),
            {"execute": lambda self, *args, **kwargs: {"industry_overview": "ok", "sources_cited": []}},
        )()
        self.orchestrator.agents["planner"] = type(
            "StubAgent",
            (),
            {"execute": lambda self, *args, **kwargs: {"objectives": ["test"], "statistical_methods": ["trend"]}},
        )()
        self.orchestrator.agents["business_translator"] = type(
            "StubAgent",
            (),
            {"execute": lambda self, *args, **kwargs: {"executive_summary": "biz", "business_narrative": "biz", "opportunities": [], "risks": [], "immediate_actions": []}},
        )()
        self.orchestrator.agents["decision_maker"] = type(
            "StubAgent",
            (),
            {"execute": lambda self, *args, **kwargs: {"executive_summary": "decision", "recommendations": [], "final_recommendation": "go"}},
        )()
        self.orchestrator.agents["presentation_architect"] = type(
            "StubAgent",
            (),
            {"execute": lambda self, *args, **kwargs: {"presentation_title": "Deck", "presentation_subtitle": "Sub", "slides": []}},
        )()

        self.orchestrator._coding_loop = lambda plan: (
            self.orchestrator.workflow_state.update(
                {
                    "analysis_results": {
                        "execution_status": "success",
                        "figures_generated": ["figure_1.png", "figure_2.png", "figure_3.png"],
                        "analysis_summary": {"average_return_pct": 2.3, "peak_volume": "2.1M"},
                        "business_findings": ["A", "B"],
                        "figure_captions": {
                            "figure_1.png": "A",
                            "figure_2.png": "B",
                            "figure_3.png": "C",
                        },
                    },
                    "saved_figures": ["figure_1.png", "figure_2.png", "figure_3.png"],
                }
            )
            or "print('cached success')"
        )

        def fail_if_reexecuted(code):
            raise AssertionError("_execute_code should not be called again after a cached successful loop result")

        self.orchestrator._execute_code = fail_if_reexecuted
        with patch.object(pipeline_runtime, "generate_pdf_report", return_value="analytics_report.pdf"), patch.object(
            pipeline_runtime, "generate_slide_deck", return_value="analytics_report.pptx"
        ):
            result = self.orchestrator.execute_workflow()

        self.assertEqual(result["status"], "completed")

    def test_execute_code_provides_df_alias_for_single_dataset(self) -> None:
        import pandas as pd

        self.orchestrator.workflow_state["csv_data"] = {
            "sample.csv": pd.DataFrame({"value": [1, 2, 3], "label": ["a", "b", "c"]})
        }

        result = self.orchestrator._execute_code(
            "\n".join(
                [
                    "analysis_summary = {'row_count': len(df), 'avg_value': float(df['value'].mean())}",
                    "business_findings = ['Average value is supported by the dataset.','Row count is available.']",
                    "figure_captions = {}",
                ]
            )
        )

        self.assertEqual(result["execution_status"], "success")
        self.assertEqual(result["analysis_summary"]["row_count"], 3)

    def test_execute_code_ignores_stale_figure_files(self) -> None:
        import pandas as pd

        self.orchestrator.workflow_state["csv_data"] = {
            "sample.csv": pd.DataFrame({"value": [1, 2, 3]})
        }

        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chdir(temp_dir)
            try:
                with open("figure_1.png", "wb") as handle:
                    handle.write(b"stale")

                result = self.orchestrator._execute_code(
                    "\n".join(
                        [
                            "analysis_summary = {'metric': 1, 'other_metric': 2}",
                            "business_findings = ['Insight one', 'Insight two']",
                            "figure_captions = {}",
                        ]
                    )
                )
            finally:
                os.chdir(original_cwd)

        self.assertEqual(result["execution_status"], "success")
        self.assertEqual(result["figures_generated"], [])

    def test_execute_code_saves_figures_with_timestamped_names(self) -> None:
        import pandas as pd

        self.orchestrator.workflow_state["csv_data"] = {
            "sample.csv": pd.DataFrame({"value": [1, 2, 3]})
        }

        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chdir(temp_dir)
            try:
                result = self.orchestrator._execute_code(
                    "\n".join(
                        [
                            "import matplotlib.pyplot as plt",
                            "plt.figure()",
                            "plt.plot(df['value'])",
                            "plt.savefig('figure_1.png')",
                            "plt.close()",
                            "analysis_summary = {'metric': 1, 'other_metric': 2}",
                            "business_findings = ['Insight one', 'Insight two']",
                            "figure_captions = {'figure_1.png': 'Line trend for the metric.'}",
                        ]
                    )
                )
            finally:
                os.chdir(original_cwd)

        self.assertEqual(result["execution_status"], "success")
        self.assertEqual(len(result["figures_generated"]), 1)
        generated_path = result["figures_generated"][0]
        self.assertIn("figure_1_", generated_path)
        self.assertTrue(generated_path.endswith(".png"))
        self.assertIn(generated_path, result["figure_captions"])
        self.assertEqual(result["figure_captions"][generated_path], "Line trend for the metric.")

    def test_coding_loop_raises_if_no_runnable_code_is_generated(self) -> None:
        class FakeCoder:
            def __init__(self) -> None:
                self.calls = 0
                self.context = {}

            def execute(self, analysis_plan, csv_data, iteration):
                self.calls += 1
                return "{not valid python"

        class FakeReviewer:
            def execute(self, *args, **kwargs):
                raise AssertionError("Reviewer should not be called when code never executes successfully.")

        coder = FakeCoder()
        self.orchestrator.agents["coder"] = coder
        self.orchestrator.agents["reviewer"] = FakeReviewer()
        self.orchestrator.workflow_state["csv_data"] = {"sample.csv": None}
        self.orchestrator._set_step = lambda *args, **kwargs: None
        self.orchestrator._execute_code = lambda code: {
            "execution_status": "failed",
            "error": "invalid syntax (<string>, line 1)",
            "traceback": "SyntaxError",
        }

        with self.assertRaisesRegex(RuntimeError, "No runnable analysis code was generated"):
            self.orchestrator._coding_loop({"objectives": ["test"]}, max_iterations=3)

        self.assertEqual(coder.calls, 3)


class CoderExtractionTests(unittest.TestCase):
    def test_extract_code_skips_explanatory_preamble(self) -> None:
        coder = DataScientistCoderAgent(
            "Data Scientist Coder",
            "Senior Data Scientist",
            "python analytics",
            openrouter_client=type("StubClient", (), {})(),
        )

        extracted = coder._extract_code(
            "Here is the Python analysis you asked for.\n\n"
            "import pandas as pd\n"
            "analysis_summary = {'rows': 3}\n"
            "business_findings = ['A', 'B']\n"
            "figure_captions = {}\n"
        )

        self.assertTrue(extracted.startswith("import pandas as pd"))
        self.assertIn("analysis_summary", extracted)


if __name__ == "__main__":
    unittest.main()
