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
            "figure_captions": {},
        }

        issues = self.orchestrator._analysis_output_issues(execution)

        self.assertGreaterEqual(len(issues), 3)
        self.assertTrue(any("fewer than 3 saved figures" in issue for issue in issues))
        self.assertTrue(any("numeric evidence" in issue for issue in issues))
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
                "Average return improved by 3.4%.",
                "Peak trading volume reached 2.8M shares.",
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
            "business_findings": [
                "Average return improved by 2.1%.",
                "Peak volume reached 2.3M shares.",
            ],
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

    def test_coding_loop_retries_when_first_runnable_code_has_warnings(self) -> None:
        class FakeCoder:
            def __init__(self) -> None:
                self.calls = 0
                self.context = {}

            def execute(self, analysis_plan, csv_data, iteration):
                self.calls += 1
                return f"print('iter {iteration}')"

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
        responses = [
            {
                "execution_status": "success",
                "figures_generated": ["figure_1.png"],
                "analysis_summary": {"headline": "Trend improved", "supporting_note": "Momentum looks better"},
                "figure_captions": {},
            },
            {
                "execution_status": "success",
                "figures_generated": ["figure_1.png", "figure_2.png", "figure_3.png"],
                "analysis_summary": {"average_return_pct": 2.1, "peak_volume": "2.3M"},
                "business_findings": [
                    "Average return improved by 2.1%.",
                    "Peak volume reached 2.3M shares.",
                ],
                "figure_captions": {
                    "figure_1.png": "Trend improved over the latest period.",
                    "figure_2.png": "Volume spikes align with the largest moves.",
                    "figure_3.png": "Momentum remained positive across the sample.",
                },
            },
        ]
        self.orchestrator._execute_code = lambda code: responses.pop(0)

        result = self.orchestrator._coding_loop({"objectives": ["test"]}, max_iterations=4)

        self.assertEqual(result, "print('iter 2')")
        self.assertEqual(coder.calls, 2)
        self.assertEqual(reviewer.calls, 1)

    def test_coding_loop_retries_when_execution_lacks_required_analysis_outputs(self) -> None:
        class FakeCoder:
            def __init__(self) -> None:
                self.calls = 0
                self.feedback_seen: list[str] = []
                self.context = {}

            def execute(self, analysis_plan, csv_data, iteration):
                self.calls += 1
                self.feedback_seen.append(str(self.context.get("review_feedback", "")))
                return f"print('iter {iteration}')"

        coder = FakeCoder()
        self.orchestrator.agents["coder"] = coder
        self.orchestrator.agents["reviewer"] = type(
            "StubReviewer",
            (),
            {"execute": lambda self, *args, **kwargs: {"decision": "REVISE"}},
        )()
        self.orchestrator.workflow_state["csv_data"] = {"sample.csv": None}
        self.orchestrator._set_step = lambda *args, **kwargs: None

        responses = [
            {
                "execution_status": "failed",
                "error": "Analysis script did not produce the required outputs: analysis_summary",
                "traceback": "",
                "figures_generated": [],
                "analysis_summary": {},
                "figure_captions": {},
            },
            {
                "execution_status": "success",
                "figures_generated": ["figure_1.png", "figure_2.png", "figure_3.png"],
                "analysis_summary": {"avg": 1.2, "peak": 5},
                "business_findings": [
                    "Average metric is 1.2.",
                    "Peak metric reached 5.",
                ],
                "figure_captions": {
                    "figure_1.png": "A",
                    "figure_2.png": "B",
                    "figure_3.png": "C",
                },
            },
        ]

        self.orchestrator._execute_code = lambda code: responses.pop(0)

        result = self.orchestrator._coding_loop({"objectives": ["test"]}, max_iterations=4)

        self.assertEqual(result, "print('iter 2')")
        self.assertEqual(coder.calls, 2)
        self.assertTrue(
            any("did not produce the required outputs" in fb for fb in coder.feedback_seen),
            f"Expected required-output feedback, got: {coder.feedback_seen}",
        )

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
                        "business_findings": [
                            "Average return improved by 2.3%.",
                            "Peak volume reached 2.1M shares.",
                        ],
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

    def test_execute_code_derives_business_findings_when_model_omits_them(self) -> None:
        import pandas as pd

        self.orchestrator.workflow_state["csv_data"] = {
            "sample.csv": pd.DataFrame({"value": [1, 2, 3]})
        }

        result = self.orchestrator._execute_code(
            "\n".join(
                [
                    "analysis_summary = {'metric': 1, 'other_metric': 2}",
                    "figure_captions = {'figure_1.png': 'Metric trend is stable.'}",
                ]
            )
        )

        self.assertEqual(result["execution_status"], "success")
        self.assertGreaterEqual(len(result["business_findings"]), 2)
        self.assertTrue(any("Metric" in item for item in result["business_findings"]))

    def test_execute_code_accepts_nan_aliases_and_normalizes_them(self) -> None:
        import pandas as pd

        self.orchestrator.workflow_state["csv_data"] = {
            "sample.csv": pd.DataFrame({"value": [1, 2, 3]})
        }

        result = self.orchestrator._execute_code(
            "\n".join(
                [
                    "analysis_summary = {'avg_value': nan, 'max_value': 3}",
                    "business_findings = ['Max value reached 3.']",
                    "figure_captions = {}",
                ]
            )
        )

        self.assertEqual(result["execution_status"], "success")
        self.assertIsNone(result["analysis_summary"]["avg_value"])
        self.assertEqual(result["analysis_summary"]["max_value"], 3)

    def test_execute_code_fails_when_required_outputs_are_missing(self) -> None:
        import pandas as pd

        self.orchestrator.workflow_state["csv_data"] = {
            "sample.csv": pd.DataFrame({"value": [1, 2, 3]})
        }

        result = self.orchestrator._execute_code("x = 1\n")

        self.assertEqual(result["execution_status"], "failed")
        self.assertIn("required outputs", result["error"])

    def test_coding_loop_retries_with_missing_dependency_feedback(self) -> None:
        class FakeCoder:
            def __init__(self) -> None:
                self.calls = 0
                self.feedback_seen: list[str] = []
                self.context: dict[str, object] = {}

            def execute(self, analysis_plan, csv_data, iteration):
                self.calls += 1
                self.feedback_seen.append(str(self.context.get("review_feedback", "")))
                return f"print('iter {iteration}')"

        coder = FakeCoder()
        self.orchestrator.agents["coder"] = coder
        self.orchestrator.agents["reviewer"] = type(
            "StubReviewer",
            (),
            {"execute": lambda self, *args, **kwargs: {"decision": "APPROVE"}},
        )()
        self.orchestrator.workflow_state["csv_data"] = {"sample.csv": None}
        self.orchestrator._set_step = lambda *args, **kwargs: None

        responses = [
            {
                "execution_status": "failed",
                "error": "No module named 'plotly'",
                "traceback": "ModuleNotFoundError",
                "missing_module": "plotly",
            },
            {
                "execution_status": "success",
                "figures_generated": ["figure_1.png", "figure_2.png", "figure_3.png"],
                "analysis_summary": {"avg": 1.2, "peak": 5},
                "business_findings": [
                    "Average metric is 1.2.",
                    "Peak metric reached 5.",
                ],
                "figure_captions": {
                    "figure_1.png": "A",
                    "figure_2.png": "B",
                    "figure_3.png": "C",
                },
            },
        ]
        self.orchestrator._execute_code = lambda code: responses.pop(0)

        result = self.orchestrator._coding_loop({"objectives": ["test"]}, max_iterations=4)

        self.assertEqual(result, "print('iter 2')")
        self.assertEqual(coder.calls, 2)
        self.assertTrue(any("plotly" in fb for fb in coder.feedback_seen))
        self.assertTrue(any("pip install plotly" in fb for fb in coder.feedback_seen))

    def test_coding_loop_logs_when_reviewer_returns_malformed_review(self) -> None:
        class FakeCoder:
            def __init__(self) -> None:
                self.calls = 0
                self.context: dict[str, object] = {}

            def execute(self, analysis_plan, csv_data, iteration):
                self.calls += 1
                return "print('try')"

        class FakeReviewer:
            def execute(self, *args, **kwargs):
                # Simulate chat_completion_json returning a parse-error envelope.
                return {"raw_text": "not json", "parse_error": "Expecting value: line 1"}

        coder = FakeCoder()
        self.orchestrator.agents["coder"] = coder
        self.orchestrator.agents["reviewer"] = FakeReviewer()
        self.orchestrator.workflow_state["csv_data"] = {"sample.csv": None}
        self.orchestrator._set_step = lambda *args, **kwargs: None
        # Execution succeeds but artifact_issues are non-empty so reviewer is consulted.
        thin_success = {
            "execution_status": "success",
            "figures_generated": ["figure_1.png"],
            "analysis_summary": {"only": "weak"},
            "figure_captions": {},
        }
        self.orchestrator._execute_code = lambda code: thin_success

        with self.assertLogs("Orchestrator", level="WARNING") as captured:
            result = self.orchestrator._coding_loop({"objectives": ["test"]}, max_iterations=2)

        self.assertEqual(result, "print('try')")
        self.assertEqual(coder.calls, 2)
        self.assertTrue(
            any("malformed review" in line for line in captured.output),
            f"Expected malformed-review WARNING, got: {captured.output}",
        )
        self.assertTrue(
            any("parse_error" in line for line in captured.output),
            f"Expected parse_error in log, got: {captured.output}",
        )

    def test_execute_code_surfaces_missing_module_name(self) -> None:
        import pandas as pd

        self.orchestrator.workflow_state["csv_data"] = {
            "sample.csv": pd.DataFrame({"value": [1, 2, 3]})
        }

        result = self.orchestrator._execute_code("import definitely_missing_lib_xyz\n")

        self.assertEqual(result["execution_status"], "failed")
        self.assertEqual(result.get("missing_module"), "definitely_missing_lib_xyz")

    def test_coding_loop_raises_if_no_runnable_code_is_generated(self) -> None:
        class FakeCoder:
            def __init__(self) -> None:
                self.calls = 0
                self.context = {}

            def execute(self, analysis_plan, csv_data, iteration):
                self.calls += 1
                raise RuntimeError("Model did not return a valid analysis script after 3 attempts.")

        coder = FakeCoder()
        self.orchestrator.agents["coder"] = coder
        self.orchestrator.workflow_state["csv_data"] = {"sample.csv": None}
        self.orchestrator._set_step = lambda *args, **kwargs: None

        with self.assertRaisesRegex(RuntimeError, "No runnable analysis code was generated"):
            self.orchestrator._coding_loop({"objectives": ["test"]}, max_iterations=3)

        self.assertEqual(coder.calls, 3)


class CoderExtractionTests(unittest.TestCase):
    def test_coder_prompt_includes_repo_analysis_skill(self) -> None:
        import pandas as pd

        class StubClient:
            def __init__(self) -> None:
                self.last_user_prompt = ""

            def chat_completion(self, system_prompt, user_prompt):
                self.last_user_prompt = user_prompt
                return (
                    "import pandas as pd\n"
                    "analysis_summary = {'rows': 1, 'avg': 2}\n"
                    "figure_captions = {}\n"
                )

        client = StubClient()
        coder = DataScientistCoderAgent(
            "Data Scientist Coder",
            "Senior Data Scientist",
            "python analytics",
            openrouter_client=client,
        )

        coder.execute({"objectives": ["test"]}, {"sample.csv": pd.DataFrame({"value": [1, 2, 3]})})

        self.assertIn("ANALYSIS SKILL:", client.last_user_prompt)
        self.assertIn("Start With Column Roles", client.last_user_prompt)

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
            "figure_captions = {}\n"
            "figure_captions = {}\n"
        )

        self.assertTrue(extracted.startswith("import pandas as pd"))
        self.assertIn("analysis_summary", extracted)

    def test_analysis_script_detector_rejects_plain_expression(self) -> None:
        coder = self._make_coder()
        weak = "{'analysis_summary': {'rows': 3}}"
        self.assertFalse(coder._looks_like_analysis_script(weak))

    def test_extract_code_drops_mixed_output_when_no_valid_script_exists(self) -> None:
        coder = self._make_coder()
        raw = (
            "Here is your answer.\n"
            "analysis_summary = {'rows': 3}\n"
            "This means the data looks healthy.\n"
            "figure_captions = {}\n"
        )
        self.assertEqual(coder._extract_code(raw), "")

    def _make_coder(self) -> DataScientistCoderAgent:
        return DataScientistCoderAgent(
            "Data Scientist Coder",
            "Senior Data Scientist",
            "python analytics",
            openrouter_client=type("StubClient", (), {})(),
        )

    def test_repair_closes_unmatched_parenthesis(self) -> None:
        coder = self._make_coder()
        broken = (
            "import pandas as pd\n"
            "analysis_summary = {'rows': 3}\n"
            "figure_captions = {}\n"
            "result = sum([1, 2, 3\n"
        )
        self.assertFalse(coder._is_compilable_python(broken))
        repaired = coder._repair_python_code(broken)
        self.assertTrue(coder._is_compilable_python(repaired))
        self.assertIn("analysis_summary", repaired)

    def test_repair_truncates_to_last_parseable_prefix(self) -> None:
        coder = self._make_coder()
        broken = (
            "import pandas as pd\n"
            "analysis_summary = {'rows': 3}\n"
            "figure_captions = {}\n"
            "def broken_fn(\n"
        )
        repaired = coder._repair_python_code(broken)
        self.assertTrue(coder._is_compilable_python(repaired))
        self.assertIn("analysis_summary", repaired)

    def test_extract_code_repairs_truncated_code_in_fenced_block(self) -> None:
        coder = self._make_coder()
        raw = (
            "```python\n"
            "import pandas as pd\n"
            "analysis_summary = {'rows': 3}\n"
            "figure_captions = {}\n"
            "result = sum([1, 2, 3\n"
            "```"
        )
        extracted = coder._extract_code(raw)
        self.assertTrue(coder._is_compilable_python(extracted))
        self.assertIn("analysis_summary", extracted)

    def test_extract_code_strips_redundant_python_label_inside_fenced_block(self) -> None:
        coder = self._make_coder()
        raw = (
            "```python\n"
            "python\n"
            "import pandas as pd\n"
            "analysis_summary = {'rows': 3, 'avg': 2}\n"
            "figure_captions = {}\n"
            "```"
        )

        extracted = coder._extract_code(raw)

        self.assertTrue(extracted.startswith("import pandas as pd"))
        self.assertNotIn("\npython\n", f"\n{extracted}\n")

    def test_repair_preserves_already_valid_code(self) -> None:
        coder = self._make_coder()
        valid = "import pandas as pd\nx = (1, 2, 3)\n"
        self.assertEqual(coder._repair_python_code(valid), valid.strip())

    def test_corrective_note_includes_syntax_error_details(self) -> None:
        coder = self._make_coder()
        broken = "import pandas as pd\nresult = sum([1, 2, 3\n"
        error = coder._python_syntax_error(broken)
        note = coder._format_syntax_corrective_note(broken, error)
        self.assertIn("SyntaxError", note)
        self.assertIn("Close every opening parenthesis", note)


if __name__ == "__main__":
    unittest.main()
