import unittest
from unittest.mock import patch
import os
import tempfile
from pathlib import Path

import analytics_workflow.pipeline_runtime as pipeline_runtime
from analytics_workflow.agents import DataScientistCoderAgent
from analytics_workflow.decision_tree_figure import (
    build_sklearn_tree_artifact,
    decision_tree_performance_note,
    humanize_decision_tree_condition,
)
from analytics_workflow.pipeline_runtime import MultiAgentOrchestrator
from analytics_workflow.runtime_config import build_runtime_config


class AnalysisLoopValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        config = build_runtime_config("openrouter-secret", "brave-secret")
        self.orchestrator = MultiAgentOrchestrator(config)

    def _structured_artifacts(self) -> list[dict]:
        return [
            {
                "artifact_id": "segment_value",
                "artifact_type": "chart_spec",
                "slide_candidate": True,
                "finding": "Segment B is five units above Segment A.",
                "chart_type": "bar",
                "title": "Segment value comparison",
                "takeaway": "Segment B leads the comparison.",
                "x": "segment",
                "y": "value",
                "data": [{"segment": "A", "value": 10}, {"segment": "B", "value": 15}],
            },
            {
                "artifact_id": "risk_ranking",
                "artifact_type": "chart_spec",
                "slide_candidate": True,
                "finding": "Risk is concentrated in the highest utilization group.",
                "chart_type": "horizontal_bar",
                "title": "Risk ranking by utilization",
                "takeaway": "High utilization requires closer review.",
                "x": "group",
                "y": "risk",
                "data": [{"group": "Low", "risk": 4}, {"group": "High", "risk": 13}],
            },
            {
                "artifact_id": "trend_line",
                "artifact_type": "chart_spec",
                "slide_candidate": True,
                "finding": "Performance improved steadily across the latest periods.",
                "chart_type": "line",
                "title": "Recent performance trend",
                "takeaway": "The latest period is the strongest point in the series.",
                "x": "period",
                "y": "score",
                "data": [{"period": "Q1", "score": 8}, {"period": "Q2", "score": 11}],
            },
            {
                "artifact_id": "segment_mix",
                "artifact_type": "chart_spec",
                "slide_candidate": True,
                "finding": "Segment mix concentrates in two operating groups.",
                "chart_type": "bar",
                "title": "Segment mix",
                "takeaway": "The largest segments define the operating baseline.",
                "x": "segment",
                "y": "share",
                "data": [{"segment": "Core", "share": 64}, {"segment": "Growth", "share": 36}],
            },
        ]

    def test_decision_tree_performance_note_caveats_high_baseline_accuracy(self) -> None:
        note = decision_tree_performance_note(
            {
                "model_type": "classification",
                "train_accuracy": "100.0%",
                "test_accuracy": "100.0%",
                "baseline_accuracy": "97.5%",
            }
        )

        self.assertIn("Exploratory screening only", note)
        self.assertIn("likely small positive class", note)
        self.assertIn("precision/recall/F1", note)
        self.assertIn("not as diagnostic or deployment-ready", note)

    def test_decision_tree_conditions_hide_model_scaled_thresholds_from_readers(self) -> None:
        self.assertEqual(
            humanize_decision_tree_condition("YearsAtCompany", "-0.753", operator="<=", split_label=True),
            "Years at company split (model-scaled)",
        )
        self.assertEqual(
            humanize_decision_tree_condition("YearsAtCompany", "-0.753", operator="<="),
            "Years at company in the lower model range",
        )
        self.assertEqual(
            humanize_decision_tree_condition("OverTime_Yes", "0.5", operator=">"),
            "Overtime is Yes",
        )

    def test_user_data_description_propagates_to_all_agents(self) -> None:
        description = "This dataset tracks telecom stock performance to support an investment decision."

        self.orchestrator.set_user_data_description(description)

        self.assertEqual(self.orchestrator.workflow_state["user_data_description"], description)
        self.assertEqual(self.orchestrator.workflow_state["workflow_objective"]["raw_description"], description)
        for agent in self.orchestrator.agents.values():
            self.assertEqual(agent.context.get("user_data_description"), description)
            self.assertEqual(agent.context.get("workflow_objective", {}).get("raw_description"), description)

    def test_decision_tree_target_propagates_to_agents(self) -> None:
        self.orchestrator.set_decision_tree_target_column("Attrition")

        self.assertEqual(self.orchestrator.workflow_state["decision_tree_target_column"], "Attrition")
        for agent in self.orchestrator.agents.values():
            self.assertEqual(agent.context.get("decision_tree_target_column"), "Attrition")

    def test_prompt_decision_tree_target_accepts_blank_or_valid_column(self) -> None:
        blank = pipeline_runtime.prompt_decision_tree_target_column(["target"], input_fn=lambda _: "")
        valid = pipeline_runtime.prompt_decision_tree_target_column(["target"], input_fn=lambda _: "target")
        invalid = pipeline_runtime.prompt_decision_tree_target_column(["target"], input_fn=lambda _: "missing")

        self.assertEqual(blank, "")
        self.assertEqual(valid, "target")
        self.assertEqual(invalid, "")

    def test_market_queries_use_objective_and_dataset_profile(self) -> None:
        self.orchestrator.set_user_data_description("Analyze revenue growth and churn risk for retail customers.")
        queries = self.orchestrator.agents["market_researcher"]._generate_queries(
            {
                "datasets": {
                    "customers.csv": {
                        "columns": ["customer_id", "revenue", "churn_flag", "region"]
                    }
                }
            }
        )

        joined = " ".join(queries).lower()
        self.assertIn("revenue", joined)
        self.assertIn("churn", joined)
        self.assertNotIn("telecom sector outlook", joined)

    def test_record_failure_stores_structured_state(self) -> None:
        self.orchestrator.workflow_state["current_step"] = 4
        self.orchestrator.workflow_state["agent_outputs"]["planner"] = {"objectives": ["x"]}

        try:
            raise RuntimeError("boom")
        except RuntimeError as exc:
            self.orchestrator._record_failure(exc)

        failure = self.orchestrator.workflow_state["failure"]
        self.assertEqual(failure["failed_step"], 4)
        self.assertEqual(failure["failed_step_name"], "Data Scientist Coder")
        self.assertIn("boom", failure["error_message"])
        self.assertIn("planner", failure["partial_outputs_present"])

    def test_detects_missing_visual_and_numeric_artifacts(self) -> None:
        execution = {
            "execution_status": "success",
            "figures_generated": ["figure_1.png"],
            "analysis_summary": {"headline": "Trend improved", "supporting_note": "Momentum looks better"},
            "figure_captions": {},
        }

        issues = self.orchestrator._analysis_output_issues(execution)

        self.assertGreaterEqual(len(issues), 3)
        self.assertTrue(any("fewer than 4 structured chart artifacts" in issue for issue in issues))
        self.assertTrue(any("numeric evidence" in issue for issue in issues))
        self.assertTrue(any("Missing figure captions" in issue for issue in issues))

    def test_targeted_run_requires_decision_tree_rules_artifact(self) -> None:
        self.orchestrator.set_decision_tree_target_column("target")
        execution = {
            "execution_status": "success",
            "figures_generated": [],
            "analysis_summary": {"rows": 100, "target_rate": "22%"},
            "business_findings": ["Target rate is 22%.", "Segment A is higher."],
            "analysis_artifacts": self._structured_artifacts(),
            "figure_captions": {},
        }

        issues = self.orchestrator._analysis_output_issues(execution)

        self.assertTrue(any("decision_tree rules artifact" in issue for issue in issues))

    def test_execute_code_builds_verified_tree_rules_with_train_test_metrics(self) -> None:
        code = """
from sklearn.tree import DecisionTreeClassifier
X_train = pd.DataFrame({'score': [0, 0, 1, 1], 'tenure': [1, 2, 8, 9]})
y_train = pd.Series(['low', 'low', 'high', 'high'])
X_test = pd.DataFrame({'score': [0, 1], 'tenure': [3, 7]})
y_test = pd.Series(['low', 'high'])
model = DecisionTreeClassifier(max_depth=2, random_state=42)
model.fit(X_train, y_train)
train_accuracy = model.score(X_train, y_train)
test_accuracy = model.score(X_test, y_test)
decision_tree_artifact = build_sklearn_tree_artifact(
    model,
    list(X_train.columns),
    target='risk',
    model_type='classification',
    train_score=train_accuracy,
    test_score=test_accuracy,
    baseline_score=0.5,
    class_names=[str(item) for item in model.classes_],
    finding='Verified leaf rules identify the high-risk segment.',
)
analysis_summary = {
    'decision_tree_train_accuracy': train_accuracy,
    'decision_tree_test_accuracy': test_accuracy,
    'rows': 6,
    'decision_tree_rules': decision_tree_artifact['data']['rules'],
}
business_findings = ['Decision tree test accuracy is 100%.', 'Leaf rules identify the high-risk segment.']
analysis_artifacts = [decision_tree_artifact]
figure_captions = {'decision_tree_rules.png': 'Decision tree leaf rules explain the target prediction.'}
render_decision_tree_rules_figure(decision_tree_artifact, 'decision_tree_rules.png')
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            cwd = os.getcwd()
            try:
                os.chdir(temp_dir)
                result = self.orchestrator._execute_code(code)
            finally:
                os.chdir(cwd)

            self.assertEqual(result["execution_status"], "success")
            tree_figures = [
                path
                for path in result["figures_generated"]
                if Path(path).name.startswith("decision_tree_rules_")
            ]
            self.assertEqual(len(tree_figures), 1)
            self.assertTrue((Path(temp_dir) / tree_figures[0]).exists())
            artifact = result["analysis_artifacts"][0]
            self.assertEqual(artifact["fallback_path"], tree_figures[0])
            self.assertTrue(artifact["data"]["model_verified"])
            self.assertTrue(artifact["data"]["rules_match_model"])
            self.assertIn("train_accuracy", artifact["data"])
            self.assertIn("test_accuracy", artifact["data"])
            self.assertTrue(artifact["data"]["edges"])
            self.assertTrue(any("True" in str(edge) for edge in artifact["data"]["edges"]))
            self.assertTrue(any("False" in str(edge) for edge in artifact["data"]["edges"]))
            self.assertTrue(any("If" in rule and "predict" in rule for rule in artifact["data"]["rules"]))
            self.assertIn(tree_figures[0], result["figure_captions"])

    def test_targeted_decision_tree_requires_verified_rules_and_train_test_metrics(self) -> None:
        self.orchestrator.set_decision_tree_target_column("target")
        with tempfile.TemporaryDirectory() as temp_dir:
            tree_path = Path(temp_dir) / "decision_tree_rules.png"
            tree_path.write_bytes(b"png")
            base_execution = {
                "execution_status": "success",
                "figures_generated": [str(tree_path)],
                "analysis_summary": {"rows": 100, "target_rate": "22%"},
                "business_findings": ["Target rate is 22%.", "Segment A is higher."],
                "figure_captions": {str(tree_path): "Decision tree rules."},
                "analysis_artifacts": self._structured_artifacts()
                + [
                    {
                        "artifact_id": "decision_tree_rules",
                        "chart_type": "decision_tree",
                        "fallback_path": str(tree_path),
                        "data": {
                            "nodes": [
                                {"id": "root", "type": "split", "label": "score <= 0.5"},
                                {"id": "left", "type": "leaf", "label": "Leaf: predict No"},
                                {"id": "right", "type": "leaf", "label": "Leaf: predict Yes"},
                            ],
                            "edges": [
                                {"source": "root", "target": "left", "label": "True"},
                                {"source": "root", "target": "right", "label": "False"},
                            ],
                        },
                    }
                ],
            }

            issues = self.orchestrator._analysis_output_issues(base_execution)
            self.assertTrue(any("not marked as verified" in issue for issue in issues))
            self.assertTrue(any("training and test model metrics" in issue for issue in issues))

            fixed_execution = dict(base_execution)
            fixed_artifacts = list(base_execution["analysis_artifacts"])
            fixed_artifacts[-1] = {
                **fixed_artifacts[-1],
                "data": {
                    **fixed_artifacts[-1]["data"],
                    "train_accuracy": "92%",
                    "test_accuracy": "84%",
                    "baseline_accuracy": "62%",
                    "model_verified": True,
                    "rules_match_model": True,
                },
            }
            fixed_execution["analysis_artifacts"] = fixed_artifacts
            self.assertEqual(self.orchestrator._analysis_output_issues(fixed_execution), [])

    def test_execute_code_enriches_weak_tree_artifact_from_runtime_model(self) -> None:
        self.orchestrator.set_decision_tree_target_column("risk")
        code = """
from sklearn.tree import DecisionTreeClassifier
X_train = pd.DataFrame({'score': [0, 0, 1, 1], 'tenure': [1, 2, 8, 9]})
y_train = pd.Series(['low', 'low', 'high', 'high'])
X_test = pd.DataFrame({'score': [0, 1], 'tenure': [3, 7]})
y_test = pd.Series(['low', 'high'])
model = DecisionTreeClassifier(max_depth=2, random_state=42)
model.fit(X_train, y_train)
analysis_summary = {'rows': 6, 'decision_tree_train_accuracy': model.score(X_train, y_train), 'decision_tree_test_accuracy': model.score(X_test, y_test)}
business_findings = ['Decision tree trained successfully.', 'Score separates the target classes.']
figure_captions = {}
analysis_artifacts = [
    {
        'artifact_id': 'decision_tree_rules',
        'artifact_type': 'chart_spec',
        'slide_candidate': True,
        'chart_type': 'decision_tree',
        'title': 'Weak tree artifact',
        'finding': 'Runtime should replace this with model-derived rules.',
        'data': {'nodes': [{'id': 'root', 'label': 'score'}], 'edges': []},
    }
]
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            cwd = os.getcwd()
            try:
                os.chdir(temp_dir)
                result = self.orchestrator._execute_code(code)
            finally:
                os.chdir(cwd)

            self.assertEqual(result["execution_status"], "success")
            artifact = next(item for item in result["analysis_artifacts"] if item["chart_type"] == "decision_tree")
            self.assertTrue(artifact["data"]["model_verified"])
            self.assertTrue(artifact["data"]["rules_match_model"])
            self.assertIn("train_accuracy", artifact["data"])
            self.assertIn("test_accuracy", artifact["data"])
            self.assertTrue(any("If" in rule and "predict" in rule for rule in artifact["data"]["rules"]))
            issues = self.orchestrator._analysis_output_issues(result)
            self.assertFalse(any("Decision tree artifact" in issue for issue in issues))
            self.assertFalse(any("not marked as verified" in issue for issue in issues))

    def test_execute_code_recovers_valid_outputs_after_late_tree_internal_error(self) -> None:
        self.orchestrator.set_decision_tree_target_column("risk")
        code = """
from sklearn.tree import DecisionTreeClassifier
X_train = pd.DataFrame({'score': [0, 0, 1, 1], 'tenure': [1, 2, 8, 9]})
y_train = pd.Series(['low', 'low', 'high', 'high'])
X_test = pd.DataFrame({'score': [0, 1], 'tenure': [3, 7]})
y_test = pd.Series(['low', 'high'])
model = DecisionTreeClassifier(max_depth=2, random_state=42)
model.fit(X_train, y_train)
plt.figure()
plt.plot([1, 2], [1, 2])
plt.title('Model evidence')
plt.savefig('figure_1.png')
plt.close()
analysis_summary = {
    'rows': 6,
    'user_goal_alignment': 'Decision tree rules identify risk segments.',
    'decision_tree_train_accuracy': model.score(X_train, y_train),
    'decision_tree_test_accuracy': model.score(X_test, y_test),
}
business_findings = ['Decision tree trained successfully.', 'Score separates the target classes.']
figure_captions = {'figure_1.png': 'Model evidence chart.'}
analysis_artifacts = [
    {
        'artifact_id': 'segment_value',
        'artifact_type': 'chart_spec',
        'slide_candidate': True,
        'finding': 'Segment B is five units above Segment A.',
        'chart_type': 'bar',
        'title': 'Segment value comparison',
        'takeaway': 'Segment B leads the comparison.',
        'x': 'segment',
        'y': 'value',
        'data': [{'segment': 'A', 'value': 10}, {'segment': 'B', 'value': 15}],
    },
    {
        'artifact_id': 'risk_ranking',
        'artifact_type': 'chart_spec',
        'slide_candidate': True,
        'finding': 'Risk is concentrated in the highest utilization group.',
        'chart_type': 'horizontal_bar',
        'title': 'Risk ranking by utilization',
        'takeaway': 'High utilization requires closer review.',
        'x': 'group',
        'y': 'risk',
        'data': [{'group': 'Low', 'risk': 4}, {'group': 'High', 'risk': 13}],
    },
    {
        'artifact_id': 'trend_line',
        'artifact_type': 'chart_spec',
        'slide_candidate': True,
        'finding': 'Performance improved steadily across the latest periods.',
        'chart_type': 'line',
        'title': 'Recent performance trend',
        'takeaway': 'The latest period is the strongest point in the series.',
        'x': 'period',
        'y': 'score',
        'data': [{'period': 'Q1', 'score': 8}, {'period': 'Q2', 'score': 11}],
    },
    {
        'artifact_id': 'segment_mix',
        'artifact_type': 'chart_spec',
        'slide_candidate': True,
        'finding': 'Segment mix concentrates in two operating groups.',
        'chart_type': 'bar',
        'title': 'Segment mix',
        'takeaway': 'The largest segments define the operating baseline.',
        'x': 'segment',
        'y': 'share',
        'data': [{'segment': 'Core', 'share': 64}, {'segment': 'Growth', 'share': 36}],
    },
]
raise AttributeError("'property' object has no attribute 'values'")
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            cwd = os.getcwd()
            try:
                os.chdir(temp_dir)
                result = self.orchestrator._execute_code(code)
            finally:
                os.chdir(cwd)

            self.assertEqual(result["execution_status"], "success")
            self.assertIn("recovered_from_error", result)
            tree_artifact = next(item for item in result["analysis_artifacts"] if item["chart_type"] == "decision_tree")
            self.assertTrue(tree_artifact["data"]["model_verified"])
            self.assertIn("train_accuracy", tree_artifact["data"])
            self.assertIn("test_accuracy", tree_artifact["data"])
            tree_names = [Path(path).name for path in result["figures_generated"] if Path(path).name.startswith("decision_tree_rules")]
            self.assertTrue(tree_names)
            self.assertFalse(any(name.startswith("decision_tree_rules_5") for name in tree_names))

    def test_build_sklearn_tree_artifact_accepts_fitted_pipeline(self) -> None:
        from sklearn.compose import ColumnTransformer
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import OneHotEncoder
        from sklearn.tree import DecisionTreeClassifier

        X = pipeline_runtime.pd.DataFrame(
            {
                "dept": ["Sales", "Sales", "HR", "HR"],
                "income": [40, 45, 70, 75],
            }
        )
        y = pipeline_runtime.pd.Series(["leave", "leave", "stay", "stay"])
        pipeline = Pipeline(
            [
                (
                    "prep",
                    ColumnTransformer(
                        [
                            ("cat", OneHotEncoder(handle_unknown="ignore"), ["dept"]),
                            ("num", "passthrough", ["income"]),
                        ]
                    ),
                ),
                ("tree", DecisionTreeClassifier(max_depth=2, random_state=42)),
            ]
        )
        pipeline.fit(X, y)

        artifact = build_sklearn_tree_artifact(
            pipeline,
            feature_names=None,
            target="Attrition",
            model_type="classification",
            train_score=pipeline.score(X, y),
            test_score=pipeline.score(X, y),
        )

        self.assertTrue(artifact["data"]["model_verified"])
        self.assertTrue(artifact["data"]["rules_match_model"])
        self.assertTrue(any("Leaf: predict" in node["label"] for node in artifact["data"]["nodes"]))
        self.assertTrue(any("income" in node["label"] or "dept" in node["label"] for node in artifact["data"]["nodes"]))

    def test_build_sklearn_tree_artifact_accepts_array_feature_names(self) -> None:
        from sklearn.tree import DecisionTreeClassifier

        X = pipeline_runtime.pd.DataFrame({"score": [0, 0, 1, 1], "tenure": [1, 2, 8, 9]})
        y = pipeline_runtime.pd.Series(["low", "low", "high", "high"])
        model = DecisionTreeClassifier(max_depth=2, random_state=42).fit(X, y)

        artifact = build_sklearn_tree_artifact(
            model,
            feature_names=pipeline_runtime.np.array(["score", "tenure"]),
            target="risk",
            model_type="classification",
            train_score=model.score(X, y),
            test_score=model.score(X, y),
        )

        self.assertTrue(artifact["data"]["model_verified"])
        self.assertTrue(any("score" in node["label"] or "tenure" in node["label"] for node in artifact["data"]["nodes"]))

    def test_pipeline_fit_is_not_misclassified_as_package_install(self) -> None:
        code = """
from sklearn.pipeline import Pipeline
model_pipeline = Pipeline([])
model_pipeline.fit
"""
        issues = self.orchestrator._analysis_code_safety_issues(code)

        self.assertFalse(any("package installation" in issue for issue in issues))

    def test_decision_tree_artifact_accepts_numeric_node_ids_and_score_aliases(self) -> None:
        self.orchestrator.set_decision_tree_target_column("Attrition")
        with tempfile.TemporaryDirectory() as temp_dir:
            tree_path = Path(temp_dir) / "decision_tree_rules.png"
            tree_path.write_bytes(b"png")
            execution = {
                "execution_status": "success",
                "figures_generated": [str(tree_path)],
                "analysis_summary": {"rows": 100, "decision_tree_test_accuracy": "77.6%"},
                "business_findings": ["Decision tree test accuracy is 77.6%.", "Income and overtime define the rules."],
                "figure_captions": {str(tree_path): "Decision tree rules."},
                "analysis_artifacts": self._structured_artifacts()
                + [
                    {
                        "artifact_id": "decision_tree_rules",
                        "chart_type": "decision_tree",
                        "fallback_path": str(tree_path),
                        "data": {
                            "model_verified": True,
                            "rules_match_model": True,
                            "model_type": "classification",
                            "train_score": 0.8134,
                            "test_score": 0.7755,
                            "baseline_accuracy": 0.62,
                            "nodes": [
                                {"id": 0, "type": "split", "rule": "MonthlyIncome <= 5109"},
                                {"id": 1, "type": "leaf", "prediction": "High Risk", "rule": "MonthlyIncome <= 5109"},
                                {"id": 2, "type": "leaf", "prediction": "Low Risk", "rule": "MonthlyIncome > 5109"},
                            ],
                            "edges": [
                                {"from": 0, "to": 1, "condition": "True"},
                                {"from": 0, "to": 2, "condition": "False"},
                            ],
                        },
                    }
                ],
            }

            issues = self.orchestrator._analysis_output_issues(execution)

            self.assertFalse(any("Decision tree artifact" in issue for issue in issues))
            normalized = self.orchestrator._normalize_chart_specs(execution["analysis_artifacts"])[-1]
            self.assertEqual(normalized["data"]["train_accuracy"], "81.3%")
            self.assertEqual(normalized["data"]["test_accuracy"], "77.5%")

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
            "analysis_artifacts": self._structured_artifacts(),
            "figure_captions": {
                "figure_1.png": "Price trend shows a positive closing pattern.",
                "figure_2.png": "Volume spikes coincide with larger moves.",
                "figure_3.png": "Returns remain concentrated around a narrow range.",
                "figure_4.png": "Rolling averages show improving momentum.",
            },
        }

        issues = self.orchestrator._analysis_output_issues(execution)

        self.assertEqual(issues, [])

    def test_image_only_analysis_warns_that_slide_visuals_need_fallback(self) -> None:
        execution = {
            "execution_status": "success",
            "figures_generated": ["figure_1.png", "figure_2.png", "figure_3.png"],
            "analysis_summary": {"average_return_pct": 3.4, "peak_volume": "2.8M shares"},
            "business_findings": [
                "Average return improved by 3.4%.",
                "Peak trading volume reached 2.8M shares.",
            ],
            "figure_captions": {
                "figure_1.png": "Price trend shows a positive closing pattern.",
                "figure_2.png": "Volume spikes coincide with larger moves.",
                "figure_3.png": "Returns remain concentrated around a narrow range.",
            },
        }

        issues = self.orchestrator._analysis_output_issues(execution)

        self.assertTrue(any("PNG figures but no structured chart artifacts" in issue for issue in issues))

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
            "analysis_artifacts": self._structured_artifacts(),
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
                return {"decision": "REVISE" if self.calls == 1 else "APPROVE"}

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
                "analysis_artifacts": self._structured_artifacts(),
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
            {"execute": lambda self, *args, **kwargs: {"decision": "APPROVE"}},
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
                "analysis_artifacts": self._structured_artifacts(),
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

    def test_coding_loop_passes_temporary_failure_context_to_next_generation(self) -> None:
        class FakeCoder:
            def __init__(self) -> None:
                self.calls = 0
                self.context = {}
                self.context_seen: list[str] = []

            def execute(self, analysis_plan, csv_data, iteration):
                self.calls += 1
                self.context_seen.append(str(self.context.get("code_loop_context_log", "")))
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
                "error": "name 'bins' is not defined",
                "traceback": "NameError: name 'bins' is not defined",
                "figures_generated": [],
                "analysis_artifacts": [],
            },
            {
                "execution_status": "success",
                "figures_generated": ["figure_1.png", "figure_2.png", "figure_3.png"],
                "analysis_summary": {"avg": 1.2, "peak": 5},
                "business_findings": ["Average metric is 1.2.", "Peak metric reached 5."],
                "analysis_artifacts": self._structured_artifacts(),
                "figure_captions": {"figure_1.png": "A", "figure_2.png": "B", "figure_3.png": "C"},
            },
        ]
        self.orchestrator._execute_code = lambda code: responses.pop(0)

        result = self.orchestrator._coding_loop({"objectives": ["test"]}, max_iterations=4)

        self.assertEqual(result, "print('iter 2')")
        self.assertEqual(coder.calls, 2)
        self.assertIn("No previous code-loop failures", coder.context_seen[0])
        self.assertIn("name 'bins' is not defined", coder.context_seen[1])
        self.assertIn("pd.cut", coder.context_seen[1])
        context_log = self.orchestrator.workflow_state["run_manifest"]["analysis_retry_context_log"]
        self.assertEqual(len(context_log), 1)
        self.assertEqual(context_log[0]["stage"], "execution")

    def test_coding_loop_adds_specific_pandas_binning_feedback(self) -> None:
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
            {"execute": lambda self, *args, **kwargs: {"decision": "APPROVE"}},
        )()
        self.orchestrator.workflow_state["csv_data"] = {"sample.csv": None}
        self.orchestrator._set_step = lambda *args, **kwargs: None

        responses = [
            {
                "execution_status": "failed",
                "error": "Bin labels must be one fewer than the number of bin edges",
                "traceback": "ValueError",
            },
            {
                "execution_status": "success",
                "figures_generated": ["figure_1.png", "figure_2.png", "figure_3.png"],
                "analysis_summary": {"avg": 1.2, "peak": 5},
                "business_findings": ["Average metric is 1.2.", "Peak metric reached 5."],
                "analysis_artifacts": self._structured_artifacts(),
                "figure_captions": {"figure_1.png": "A", "figure_2.png": "B", "figure_3.png": "C"},
            },
        ]
        self.orchestrator._execute_code = lambda code: responses.pop(0)

        result = self.orchestrator._coding_loop({"objectives": ["test"]}, max_iterations=4)

        self.assertEqual(result, "print('iter 2')")
        self.assertTrue(any("len(bin_edges) - 1" in fb for fb in coder.feedback_seen))
        self.assertTrue(any("pd.qcut" in fb for fb in coder.feedback_seen))

    def test_known_execution_guidance_handles_missing_metric_labels(self) -> None:
        guidance = self.orchestrator._known_execution_error_guidance("'avg_summer_temp'")

        self.assertTrue(any("metric/value summary tables" in item for item in guidance))
        self.assertTrue(any("invented normalized keys" in item for item in guidance))

    def test_known_execution_guidance_gives_concrete_decision_tree_pattern(self) -> None:
        self.orchestrator.set_decision_tree_target_column("depression_label")
        helper_import = self.orchestrator._known_execution_error_guidance(
            "Unsafe analysis code blocked: import is not in the analysis allowlist: sklearn_utils"
        )
        tree_preflight = self.orchestrator._known_execution_error_guidance(
            "Analysis code preflight failed: decision tree preflight: do not inspect sklearn tree internals"
        )

        self.assertTrue(any("Remove the fake sklearn_utils import" in item for item in helper_import))
        self.assertTrue(any("already available" in item for item in helper_import))
        self.assertTrue(any("Mandatory decision-tree pattern" in item for item in tree_preflight))
        self.assertTrue(any("depression_label" in item for item in tree_preflight))
        self.assertTrue(any("render_decision_tree_rules_figure" in item for item in tree_preflight))

    def test_known_execution_guidance_handles_filesystem_imports_and_dtype_labels(self) -> None:
        blocked = self.orchestrator._known_execution_error_guidance(
            "Unsafe analysis code blocked: import is not in the analysis allowlist: os"
        )
        dtype = self.orchestrator._known_execution_error_guidance(
            "Invalid value 'June' for dtype 'float64'"
        )

        self.assertTrue(any("direct figure_#.png names" in item for item in blocked))
        self.assertTrue(any("month names" in item for item in dtype))

    def test_known_execution_guidance_handles_eda_visual_payload_errors(self) -> None:
        lengths = self.orchestrator._known_execution_error_guidance("All arrays must be of the same length")
        colors = self.orchestrator._known_execution_error_guidance(
            "'facecolor' or 'color' argument must be a valid color or sequence of colors."
        )
        palette = self.orchestrator._known_execution_error_guidance(
            "The palette dictionary is missing keys: {'0', '1'}"
        )
        categories = self.orchestrator._known_execution_error_guidance(
            "Object with dtype category cannot perform the numpy op add"
        )

        self.assertTrue(any("matching length" in item for item in lengths))
        self.assertTrue(any("valid matplotlib color strings" in item for item in colors))
        self.assertTrue(any("exact observed hue values" in item for item in palette))
        self.assertTrue(any(".astype(str)" in item for item in palette))
        self.assertTrue(any("category labels to strings" in item for item in categories))

    def test_known_execution_guidance_covers_weather_generation_failures(self) -> None:
        string_mean = self.orchestrator._known_execution_error_guidance(
            "dtype 'str' does not support operation 'mean'"
        )
        shadowed_stats = self.orchestrator._known_execution_error_guidance(
            "'dict' object has no attribute 'pearsonr'"
        )
        undefined = self.orchestrator._known_execution_error_guidance("name 'temp_trend' is not defined")

        self.assertTrue(any("numeric metric columns" in item for item in string_mean))
        self.assertTrue(any("do not shadow scipy.stats" in item for item in shadowed_stats))
        self.assertTrue(any("undefined variables" in item.lower() for item in undefined))

    def test_known_execution_guidance_covers_full_run_retry_failures(self) -> None:
        charmap = self.orchestrator._known_execution_error_guidance(
            "'charmap' codec can't encode character '\\u2713'"
        )
        category = self.orchestrator._known_execution_error_guidance("'DEBT_CONSOLIDATION'")
        bins = self.orchestrator._known_execution_error_guidance("name 'bins' is not defined")
        fillna = self.orchestrator._known_execution_error_guidance(
            "NDFrame.fillna() got an unexpected keyword argument 'method'"
        )
        pandas_frequency = self.orchestrator._known_execution_error_guidance(
            "Invalid frequency: M. Failed to parse with error message: ValueError(\"'M' is no longer supported for offsets. Please use 'ME' instead.\")"
        )
        private_imputer = self.orchestrator._known_execution_error_guidance(
            "'KNNImputer' object has no attribute '_find_nearest_neighbors'"
        )
        compact_number = self.orchestrator._known_execution_error_guidance(
            "could not convert string to float: '331.82K'"
        )
        scalar_iter = self.orchestrator._known_execution_error_guidance(
            "'numpy.float64' object is not iterable"
        )
        missing_outputs = self.orchestrator._known_execution_error_guidance(
            "Analysis script did not produce the required outputs: analysis_summary, business_findings"
        )
        undefined_generated = self.orchestrator._known_execution_error_guidance(
            "Analysis code preflight failed: undefined generated name: satisfaction_attraction; did you mean satisfaction_attrition?"
        )
        tree_preflight = self.orchestrator._known_execution_error_guidance(
            "Analysis code preflight failed: decision tree preflight: do not inspect sklearn tree internals"
        )

        self.assertTrue(any("ASCII-safe" in item for item in charmap))
        self.assertTrue(any("do not invent category values" in item for item in category))
        self.assertTrue(any("exact column names" in item for item in category))
        self.assertTrue(any("define bin_edges or bins" in item for item in bins))
        self.assertTrue(any(".ffill()" in item for item in fillna))
        self.assertTrue(any("'ME'" in item and "'YE'" in item for item in pandas_frequency))
        self.assertTrue(any("to_period('M')" in item for item in pandas_frequency))
        self.assertTrue(any("private sklearn internals" in item for item in private_imputer))
        self.assertTrue(any("331.82K" in item for item in compact_number))
        self.assertTrue(any("wrap scalar" in item for item in scalar_iter))
        self.assertTrue(any("assign analysis_summary" in item for item in missing_outputs))
        self.assertTrue(any("undefined generated names" in item for item in undefined_generated))
        self.assertTrue(any("build_sklearn_tree_artifact" in item for item in tree_preflight))

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
                    "business_findings = ['Dataset contains 3 rows.', 'Average value is 2.']",
                    "figure_captions = {}",
                    "analysis_artifacts = []",
                ]
            )
        )

        self.assertEqual(result["execution_status"], "success")
        self.assertEqual(result["analysis_summary"]["row_count"], 3)

    def test_execute_code_makes_print_output_ascii_safe(self) -> None:
        import pandas as pd

        self.orchestrator.workflow_state["csv_data"] = {
            "sample.csv": pd.DataFrame({"segment": ["A", "B"], "value": [10, 15]})
        }

        result = self.orchestrator._execute_code(
            "\n".join(
                [
                    "print('✓ analysis ready')",
                    "analysis_summary = {'rows': len(df), 'mean_value': float(df['value'].mean())}",
                    "business_findings = ['Mean value is 12.5.']",
                    "figure_captions = {}",
                    (
                        "analysis_artifacts = [{'artifact_id': 'mean_value', "
                        "'artifact_type': 'chart_spec', 'chart_type': 'bar', "
                        "'title': 'Mean value', 'takeaway': 'Mean value is 12.5.', "
                        "'data': [{'label': 'Mean', 'value': 12.5}]}]"
                    ),
                ]
            )
        )

        self.assertEqual(result["execution_status"], "success")
        self.assertEqual(result["analysis_summary"]["rows"], 2)

    def test_analysis_code_preflight_blocks_known_bad_generated_patterns(self) -> None:
        cases = {
            "df.fillna(method='ffill')": "fillna(method=...)",
            "df.resample('H').mean()": "offset aliases",
            "imputer._find_nearest_neighbors(X)": "private sklearn",
            "pd.cut(df['x'], bins=bins, labels=['Low', 'High'])": "fixed pd.cut labels",
            "pd.cut(df['x'], bins=[0, 10, 20], labels=['Low'])": "pd.cut labels must match bins",
            "df.groupby('segment').mean()": "groupby(...).mean()",
            "for value in np.float64(1.2):\n    pass": "numpy scalar",
            "DecisionTreeClassifier.tree_.value": "decision tree preflight",
            "children_left = model.tree_.children_left": "decision tree preflight",
            "values = getattr(DecisionTreeClassifier, 'tree_').values": "decision tree preflight",
            "TreeModel = DecisionTreeClassifier\nvalues = getattr(getattr(TreeModel, 'tree_'), 'values')": "decision tree preflight",
            "build_sklearn_tree_artifact(DecisionTreeClassifier, target='Attrition')": "not the DecisionTreeClassifier",
            "build_sklearn_tree_artifact(model=DecisionTreeRegressor, target='Risk')": "not the DecisionTreeClassifier",
            "TreeModel = DecisionTreeClassifier\nbuild_sklearn_tree_artifact(TreeModel, target='Attrition')": "not the DecisionTreeClassifier",
            "from sklearn.tree import DecisionTreeClassifier as DTC\nTreeModel = DTC\nbuild_sklearn_tree_artifact(TreeModel, target='Attrition')": "not the DecisionTreeClassifier",
            "build_sklearn_tree_artifact(model, feature_names=model.feature_names_in_)": "feature_names_in_",
            "row = {'value': 1}\nmedian_value = row['median']": "fragile metric lookup",
            "key = 'median'\nrow = {'value': 1}\nmedian_value = row[key]": "fragile metric lookup",
            "satisfaction_attrition = 1\nresult = satisfaction_attraction": "satisfaction_attrition",
        }

        for code, expected in cases.items():
            with self.subTest(code=code):
                issues = self.orchestrator._analysis_code_preflight_issues(code)
                self.assertTrue(any(expected in issue for issue in issues), issues)

        safe_bins = self.orchestrator._analysis_code_preflight_issues(
            "pd.cut(df['x'], bins=[0, 10, 20], labels=['Low', 'High'])"
        )
        self.assertFalse(any("pd.cut" in issue for issue in safe_bins), safe_bins)

        safe_literal_metric = self.orchestrator._analysis_code_preflight_issues(
            "stats = {'median': 4.5}\nmedian_value = stats['median']"
        )
        self.assertFalse(any("fragile metric lookup" in issue for issue in safe_literal_metric), safe_literal_metric)

        safe_describe_metric = self.orchestrator._analysis_code_preflight_issues(
            "stats = df['close'].describe()\nmean_value = stats['mean']"
        )
        self.assertFalse(any("fragile metric lookup" in issue for issue in safe_describe_metric), safe_describe_metric)

    def test_runtime_tree_estimator_ignores_unfitted_tree_classes(self) -> None:
        from sklearn.tree import DecisionTreeClassifier

        self.assertIsNone(self.orchestrator._tree_estimator(DecisionTreeClassifier))
        self.assertIsNone(self.orchestrator._runtime_decision_tree_model_context({"DecisionTreeClassifier": DecisionTreeClassifier}))

    def test_runtime_tree_context_skips_imported_class_and_uses_fitted_model(self) -> None:
        from sklearn.tree import DecisionTreeClassifier

        X_train = pipeline_runtime.pd.DataFrame({"score": [0, 0, 1, 1], "tenure": [1, 2, 8, 9]})
        y_train = pipeline_runtime.pd.Series(["low", "low", "high", "high"])
        model = DecisionTreeClassifier(max_depth=2, random_state=42).fit(X_train, y_train)

        context = self.orchestrator._runtime_decision_tree_model_context(
            {
                "DecisionTreeClassifier": DecisionTreeClassifier,
                "model": model,
                "X_train": X_train,
                "y_train": y_train,
            }
        )

        self.assertIsNotNone(context)
        self.assertIs(context["model"], model)
        self.assertEqual(context["feature_names"], ["score", "tenure"])

    def test_build_sklearn_tree_artifact_accepts_model_alias_keyword(self) -> None:
        from sklearn.tree import DecisionTreeClassifier

        X = pipeline_runtime.pd.DataFrame({"score": [0, 0, 1, 1]})
        y = pipeline_runtime.pd.Series(["low", "low", "high", "high"])
        model = DecisionTreeClassifier(max_depth=2, random_state=42).fit(X, y)

        artifact = build_sklearn_tree_artifact(
            fitted_model_or_pipeline=model,
            feature_names=None,
            target="risk",
            model_type="classification",
            train_score=model.score(X, y),
            test_score=model.score(X, y),
            baseline_score=0.5,
        )

        self.assertTrue(artifact["data"]["model_verified"])
        self.assertTrue(artifact["data"]["edges"])

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
                            "business_findings = ['Metric is 1.', 'Other metric is 2.']",
                            "figure_captions = {}",
                            "analysis_artifacts = []",
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
                            "business_findings = ['Metric is 1.', 'Other metric is 2.']",
                            "figure_captions = {'figure_1.png': 'Line trend for the metric.'}",
                            "analysis_artifacts = []",
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

    def test_execute_code_allows_divmod_for_plot_grid_layouts(self) -> None:
        import pandas as pd

        self.orchestrator.workflow_state["csv_data"] = {
            "sample.csv": pd.DataFrame({"value": [1, 2, 3]})
        }

        result = self.orchestrator._execute_code(
            "\n".join(
                [
                    "row, col = divmod(5, 2)",
                    "analysis_summary = {'rows': len(df), 'grid_row': row, 'grid_col': col}",
                    "business_findings = ['Grid layout calculation completed.']",
                    "figure_captions = {}",
                    "analysis_artifacts = []",
                ]
            )
        )

        self.assertEqual(result["execution_status"], "success")
        self.assertEqual(result["analysis_summary"]["grid_row"], 2)
        self.assertEqual(result["analysis_summary"]["grid_col"], 1)

    def test_execute_code_rejects_missing_business_findings(self) -> None:
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

        self.assertEqual(result["execution_status"], "failed")
        self.assertIn("business_findings", result["error"])

    def test_execute_code_captures_optional_chart_specs(self) -> None:
        import pandas as pd

        self.orchestrator.workflow_state["csv_data"] = {
            "sample.csv": pd.DataFrame({"segment": ["A", "B"], "value": [10, 15]})
        }

        result = self.orchestrator._execute_code(
            "\n".join(
                [
                    "analysis_summary = {'top_segment_value': 15, 'segment_count': 2}",
                    "business_findings = ['Segment B is five units above Segment A.']",
                    "figure_captions = {}",
                    "analysis_artifacts = []",
                    "chart_specs = [{",
                    "  'id': 'segment_value',",
                    "  'chart_type': 'bar',",
                    "  'title': 'Segment value comparison',",
                    "  'takeaway': 'Segment B leads the comparison.',",
                    "  'x': 'segment',",
                    "  'y': 'value',",
                    "  'data': [{'segment': 'A', 'value': 10}, {'segment': 'B', 'value': 15}]",
                    "}]",
                ]
            )
        )

        self.assertEqual(result["execution_status"], "success")
        self.assertEqual(len(result["chart_specs"]), 1)
        self.assertEqual(len(result["analysis_artifacts"]), 1)
        self.assertEqual(result["chart_specs"][0]["chart_type"], "bar")
        self.assertEqual(result["chart_specs"][0]["data"][1]["segment"], "B")

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
                    "analysis_artifacts = []",
                ]
            )
        )

        self.assertEqual(result["execution_status"], "success")
        self.assertIsNone(result["analysis_summary"]["avg_value"])
        self.assertEqual(result["analysis_summary"]["max_value"], 3)

    def test_execute_code_allows_locals_for_optional_variable_checks(self) -> None:
        import pandas as pd

        self.orchestrator.workflow_state["csv_data"] = {
            "sample.csv": pd.DataFrame({"value": [1, 2, 3]})
        }

        result = self.orchestrator._execute_code(
            "\n".join(
                [
                    "has_df = 'df' in locals()",
                    "analysis_summary = {'has_df': has_df, 'rows': len(df)}",
                    "business_findings = ['Dataset contains 3 rows.', 'The df alias is available.']",
                    "figure_captions = {}",
                    "analysis_artifacts = []",
                ]
            )
        )

        self.assertEqual(result["execution_status"], "success")
        self.assertTrue(result["analysis_summary"]["has_df"])

    def test_execute_code_blocks_subprocess_and_runtime_install_patterns(self) -> None:
        import pandas as pd

        self.orchestrator.workflow_state["csv_data"] = {
            "sample.csv": pd.DataFrame({"value": [1, 2, 3]})
        }

        result = self.orchestrator._execute_code("import subprocess\nsubprocess.check_call(['pip', 'install', 'x'])\n")

        self.assertEqual(result["execution_status"], "failed")
        self.assertIn("Unsafe analysis code blocked", result["error"])
        self.assertTrue(any("subprocess" in issue for issue in result.get("safety_issues", [])))

    def test_execute_code_fails_when_required_outputs_are_missing(self) -> None:
        import pandas as pd

        self.orchestrator.workflow_state["csv_data"] = {
            "sample.csv": pd.DataFrame({"value": [1, 2, 3]})
        }

        result = self.orchestrator._execute_code("x = 1\n")

        self.assertEqual(result["execution_status"], "failed")
        self.assertIn("analysis_summary", result["error"])
        self.assertIn("business_findings", result["error"])

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
                "analysis_artifacts": self._structured_artifacts(),
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
        self.assertTrue(any("allowed analytics packages" in fb for fb in coder.feedback_seen))

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
            with self.assertRaisesRegex(RuntimeError, "No reviewer-approved analysis code"):
                self.orchestrator._coding_loop({"objectives": ["test"]}, max_iterations=2)
        self.assertEqual(coder.calls, 2)
        self.assertTrue(
            any("malformed review" in line for line in captured.output),
            f"Expected malformed-review WARNING, got: {captured.output}",
        )
        self.assertTrue(
            any("parse_error" in line for line in captured.output),
            f"Expected parse_error in log, got: {captured.output}",
        )

    def test_execute_code_blocks_unsafe_or_unapproved_imports(self) -> None:
        import pandas as pd

        self.orchestrator.workflow_state["csv_data"] = {
            "sample.csv": pd.DataFrame({"value": [1, 2, 3]})
        }

        result = self.orchestrator._execute_code("import definitely_missing_lib_xyz\n")

        self.assertEqual(result["execution_status"], "failed")
        self.assertIn("Unsafe analysis code blocked", result["error"])
        self.assertTrue(any("definitely_missing_lib_xyz" in issue for issue in result.get("safety_issues", [])))

    def test_execute_code_installs_missing_approved_package_before_running(self) -> None:
        import pandas as pd

        self.orchestrator.workflow_state["csv_data"] = {
            "sample.csv": pd.DataFrame({"value": [1, 2, 3]})
        }
        code = "\n".join(
            [
                "import numpy",
                "analysis_summary = {'rows': len(df), 'metric': 3}",
                "business_findings = ['Dataset contains 3 rows.', 'Metric reached 3.']",
                "figure_captions = {}",
                "analysis_artifacts = []",
            ]
        )

        with patch("analytics_workflow.pipeline_runtime.importlib.util.find_spec", return_value=None), patch(
            "analytics_workflow.pipeline_runtime.subprocess.check_call", return_value=0
        ) as install:
            result = self.orchestrator._execute_code(code)

        self.assertEqual(result["execution_status"], "success")
        install.assert_called_once()
        self.assertIn("numpy", install.call_args.args[0])

    def test_execute_code_reports_failed_approved_package_install(self) -> None:
        import pandas as pd

        self.orchestrator.workflow_state["csv_data"] = {
            "sample.csv": pd.DataFrame({"value": [1, 2, 3]})
        }

        with patch("analytics_workflow.pipeline_runtime.importlib.util.find_spec", return_value=None), patch(
            "analytics_workflow.pipeline_runtime.subprocess.check_call", side_effect=RuntimeError("install failed")
        ):
            result = self.orchestrator._execute_code("import plotly\n")

        self.assertEqual(result["execution_status"], "failed")
        self.assertIn("Approved package install failed", result["error"])
        self.assertEqual(result.get("missing_module"), "plotly")

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

        with self.assertRaisesRegex(RuntimeError, "No reviewer-approved analysis code"):
            self.orchestrator._coding_loop({"objectives": ["test"]}, max_iterations=3)

        self.assertEqual(coder.calls, 3)


class CoderExtractionTests(unittest.TestCase):
    def test_coder_prompt_includes_repo_analysis_skill(self) -> None:
        import pandas as pd

        class StubClient:
            def __init__(self) -> None:
                self.last_user_prompt = ""

            def chat_completion(self, system_prompt, user_prompt, **kwargs):
                self.last_user_prompt = user_prompt
                return (
                    "import pandas as pd\n"
                    "analysis_summary = {'rows': 1, 'avg': 2}\n"
                    "business_findings = ['Rows total 1.', 'Average is 2.']\n"
                    "figure_captions = {}\n"
                    "analysis_artifacts = []\n"
                )

        client = StubClient()
        coder = DataScientistCoderAgent(
            "Data Scientist Coder",
            "Senior Data Scientist",
            "python analytics",
            openrouter_client=client,
        )
        coder.context["decision_tree_target_column"] = "value"
        coder.context["data_understanding"] = {
            "datasets": {
                "sample.csv": {
                    "column_profiles": {
                        "customer_id": {"role": "identifier"},
                        "value": {"role": "numeric-continuous"},
                    }
                }
            }
        }

        coder.execute(
            {"objectives": ["test"]},
            {"sample.csv": pd.DataFrame({"customer_id": [101, 102, 103], "value": [1, 2, 3]})},
        )

        self.assertIn("ANALYSIS SKILL:", client.last_user_prompt)
        self.assertIn("# Code Generation Skill", client.last_user_prompt)
        self.assertIn("Choose visuals from the data-understanding column roles", client.last_user_prompt)
        self.assertNotIn("pip install", client.last_user_prompt)
        self.assertIn("approved analytics packages", client.last_user_prompt)
        self.assertIn("DATA PROFILE FOR CODE GENERATION:", client.last_user_prompt)
        self.assertIn("DATA UNDERSTANDING OUTPUT:", client.last_user_prompt)
        self.assertIn("SUITED VISUAL PLAN FROM DATA UNDERSTANDING:", client.last_user_prompt)
        self.assertIn('"role_hint": "identifier"', client.last_user_prompt)
        self.assertIn("comfortable slide reading", client.last_user_prompt)
        self.assertIn("muted colorblind-aware palette", client.last_user_prompt)
        self.assertIn("#1f4e79", client.last_user_prompt)
        self.assertIn("Do not create randomized or decorative charts", client.last_user_prompt)
        self.assertIn(".ffill()", client.last_user_prompt)
        self.assertIn("long-form metric/value summary tables", client.last_user_prompt)
        self.assertIn("do not import `os`, `pathlib`", client.last_user_prompt)
        self.assertIn("aligned aggregated rows", client.last_user_prompt)
        self.assertIn("DECISION TREE MODEL REQUEST:", client.last_user_prompt)
        self.assertIn("Train exactly one interpretable decision tree model", client.last_user_prompt)
        self.assertIn("chart_type='decision_tree'", client.last_user_prompt)
        self.assertIn("build_sklearn_tree_artifact", client.last_user_prompt)
        self.assertIn("training accuracy, test accuracy", client.last_user_prompt)

    def test_suited_visual_plan_uses_target_segments_time_and_numeric_pairings(self) -> None:
        import pandas as pd

        coder = DataScientistCoderAgent(
            "Data Scientist Coder",
            "Senior Data Scientist",
            "python analytics",
            openrouter_client=type("StubClient", (), {})(),
        )

        plan = coder._suited_visual_plan(
            {
                "hr.csv": pd.DataFrame(
                    {
                        "Attrition": ["Yes", "No", "No", "Yes"],
                        "Department": ["Sales", "HR", "Sales", "Tech"],
                        "MonthlyIncome": [3000, 5000, 4500, 7000],
                        "YearsAtCompany": [1, 5, 3, 8],
                    }
                )
            },
            {"visualization_plan": ["compare attrition by department"]},
        )

        hr_plan = plan["hr.csv"]
        self.assertTrue(any(item["chart_type"] == "horizontal_bar" for item in hr_plan))
        self.assertTrue(any("Attrition" in item["columns"] for item in hr_plan))
        self.assertTrue(any(item["chart_type"] == "scatter" for item in hr_plan))
        self.assertIn("_planner_visualization_plan", plan)

    def test_suited_visual_plan_handles_split_hourly_weather_components(self) -> None:
        import pandas as pd

        coder = DataScientistCoderAgent(
            "Data Scientist Coder",
            "Senior Data Scientist",
            "python analytics",
            openrouter_client=type("StubClient", (), {})(),
        )

        plan = coder._suited_visual_plan(
            {
                "power.csv": pd.DataFrame(
                    {
                        "YEAR": [2007, 2007, 2007, 2007],
                        "MO": [1, 1, 1, 1],
                        "DY": [1, 1, 1, 1],
                        "HR": [0, 1, 2, 3],
                        "CLRSKY_SFC_SW_DWN": [0, 0, 0, 10],
                        "T2M": [18.2, 17.7, 17.4, 17.0],
                        "RH2M": [68.7, 68.8, 68.7, 68.6],
                        "WS10M": [5.27, 4.85, 4.58, 4.42],
                    }
                )
            },
            {},
        )

        weather_plan = plan["power.csv"]
        self.assertEqual(weather_plan[0]["chart_type"], "line")
        self.assertIn("YEAR", weather_plan[0]["columns"])
        self.assertIn("T2M", weather_plan[0]["columns"])
        self.assertTrue(any("timestamp" in item["method"] for item in weather_plan))
        self.assertTrue(any(item["columns"] == ["HR", "CLRSKY_SFC_SW_DWN"] for item in weather_plan))
        self.assertFalse(any(item["columns"] == ["YEAR"] for item in weather_plan))

    def test_suited_visual_plan_prioritizes_stock_time_series_over_dense_scatter(self) -> None:
        import pandas as pd

        coder = DataScientistCoderAgent(
            "Data Scientist Coder",
            "Senior Data Scientist",
            "python analytics",
            openrouter_client=type("StubClient", (), {})(),
        )

        plan = coder._suited_visual_plan(
            {
                "stc.csv": pd.DataFrame(
                    {
                        "Date": pd.date_range("2026-01-01", periods=5),
                        "Open": [40, 41, 42, 41, 43],
                        "High": [42, 43, 43, 44, 45],
                        "Low": [39, 40, 41, 40, 42],
                        "Price": [41, 42, 41, 43, 44],
                        "Vol.": ["331.82K", "2.83M", "1.10M", "980.00K", "1.50M"],
                        "Change %": ["0.4%", "-0.2%", "1.1%", "0.6%", "-0.1%"],
                    }
                )
            },
            {},
        )

        stock_plan = plan["stc.csv"]
        questions = " ".join(item["question"].lower() for item in stock_plan)
        methods = " ".join(item["method"].lower() for item in stock_plan)

        self.assertTrue(any(item["chart_type"] == "line" for item in stock_plan))
        self.assertIn("volatility", questions)
        self.assertIn("return distribution", questions)
        self.assertIn("avoid dense volume-return scatter", methods)
        self.assertIn("slide-readable rolling risk chart", methods)
        self.assertIn("do not create separate dense volatility and drawdown slides", methods)
        self.assertFalse(any(item["chart_type"] == "scatter" for item in stock_plan))

    def test_coder_raises_after_invalid_model_output_instead_of_falling_back(self) -> None:
        import pandas as pd

        class StubClient:
            def chat_completion(self, system_prompt, user_prompt, **kwargs):
                return "This is not Python analysis code."

        coder = DataScientistCoderAgent(
            "Data Scientist Coder",
            "Senior Data Scientist",
            "python analytics",
            openrouter_client=StubClient(),
        )

        with self.assertRaisesRegex(RuntimeError, "did not return a valid analysis script"):
            coder.execute(
                {"objectives": ["profile the dataset"]},
                {"sample.csv": pd.DataFrame({"segment": ["A", "B", "A"], "value": [10, 15, 12]})},
            )

    def test_coder_reports_generation_attempt_progress(self) -> None:
        import pandas as pd

        class StubClient:
            def chat_completion(self, system_prompt, user_prompt, **kwargs):
                return (
                    "import pandas as pd\n"
                    "analysis_summary = {'rows': 3, 'average_value': 12.3}\n"
                    "business_findings = ['Rows total 3.', 'Average value is 12.3.']\n"
                    "figure_captions = {}\n"
                    "analysis_artifacts = []\n"
                )

        statuses: list[str] = []
        coder = DataScientistCoderAgent(
            "Data Scientist Coder",
            "Senior Data Scientist",
            "python analytics",
            openrouter_client=StubClient(),
        )
        coder.context["progress_callback"] = statuses.append

        code = coder.execute(
            {"objectives": ["profile the dataset"]},
            {"sample.csv": pd.DataFrame({"segment": ["A", "B", "A"], "value": [10, 15, 12]})},
            iteration=2,
        )

        self.assertIn("analysis_artifacts", code)
        self.assertTrue(any("waiting for model code (iteration 2, attempt 1/2)" in status for status in statuses))
        self.assertTrue(any("model code received (iteration 2, attempt 1/2)" in status for status in statuses))

    def test_dataset_generation_context_profiles_values_for_code_fit(self) -> None:
        import pandas as pd
        coder = DataScientistCoderAgent(
            "Data Scientist Coder",
            "Senior Data Scientist",
            "python analytics",
            openrouter_client=type("StubClient", (), {})(),
        )

        context = coder._dataset_generation_context(
            {
                "sample.csv": pd.DataFrame(
                    {
                        "customer_id": [101, 102, 103, 104],
                        "segment": ["A", "B", "A", "C"],
                        "value": [10.0, None, 12.0, 15.0],
                    }
                )
            }
        )["sample.csv"]

        self.assertEqual(context["shape"], [4, 3])
        self.assertEqual(context["profiled_columns"]["customer_id"]["role_hint"], "identifier")
        self.assertEqual(context["profiled_columns"]["segment"]["sample_values"], ["A", "B", "A"])
        self.assertEqual(context["profiled_columns"]["value"]["role_hint"], "numeric-discrete")
        self.assertEqual(context["profiled_columns"]["value"]["missing_pct"], 25.0)
        self.assertEqual(context["profiled_columns"]["value"]["numeric_range"]["max"], 15.0)

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
            "business_findings = ['Rows total 3.', 'Dataset is profiled.']\n"
            "figure_captions = {}\n"
            "analysis_artifacts = []\n"
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
            "business_findings = ['Rows total 3.', 'Dataset is profiled.']\n"
            "figure_captions = {}\n"
            "analysis_artifacts = []\n"
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
            "business_findings = ['Rows total 3.', 'Dataset is profiled.']\n"
            "figure_captions = {}\n"
            "analysis_artifacts = []\n"
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
            "business_findings = ['Rows total 3.', 'Dataset is profiled.']\n"
            "figure_captions = {}\n"
            "analysis_artifacts = []\n"
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
            "business_findings = ['Rows total 3.', 'Average is 2.']\n"
            "figure_captions = {}\n"
            "analysis_artifacts = []\n"
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
