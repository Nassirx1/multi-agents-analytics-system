from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

import analytics_workflow.cli as cli
from analytics_workflow.html_dashboard.contracts import DashboardErrorCode, DashboardWorkflowError
from analytics_workflow.html_dashboard.planning import HTMLDashboardPlanningAgent, normalize_dashboard_plan
from analytics_workflow.html_dashboard.renderer import render_dashboard, validate_dashboard_html
from analytics_workflow.html_dashboard.workflow import HTMLDashboardWorkflow, confined_path
from analytics_workflow.output_paths import OutputPath, coerce_output_path, prompt_output_path
from analytics_workflow.pipeline_runtime import MultiAgentOrchestrator, run_non_interactive_workflow
from analytics_workflow.runtime_config import build_runtime_config
from analytics_workflow.workflow_steps import workflow_steps_for


def _config(**overrides):
    values = {
        "openrouter_api_key": "openrouter-secret",
        "brave_search_api_key": "brave-secret",
        "html_dashboard_stage_timeout_seconds": 30,
        "html_dashboard_max_rows": 1000,
    }
    values.update(overrides)
    return build_runtime_config(**values)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Department": ["Sales", "Sales", "HR", "Engineering"],
            "Attrition": ["Yes", "No", "No", "Yes"],
            "MonthlyIncome": [5000, 7000, 4500, 9000],
            "YearsAtCompany": [2, 5, 8, 3],
        }
    )


def _candidate_plan() -> dict:
    return {
        "project_name": "Employee Dashboard",
        "title": "Employee Overview",
        "subtitle": "Workforce composition and attrition",
        "filters": [{"dataset": "employees.csv", "field": "Department", "label": "Department"}],
        "kpis": [
            {
                "dataset": "employees.csv",
                "label": "Employees",
                "field": "Department",
                "aggregation": "count",
                "format": "integer",
                "description": "Rows in the selected view",
            },
            {
                "dataset": "employees.csv",
                "label": "Average income",
                "field": "MonthlyIncome",
                "aggregation": "mean",
                "format": "currency",
                "description": "Mean monthly income",
            },
        ],
        "pages": [
            {
                "name": "Overview",
                "purpose": "Primary workforce breakdowns",
                "charts": [
                    {
                        "dataset": "employees.csv",
                        "title": "Employees by department",
                        "type": "bar",
                        "x": "Department",
                        "y": "",
                        "aggregation": "count",
                        "description": "Workforce distribution",
                    },
                    {
                        "dataset": "employees.csv",
                        "title": "Income by tenure",
                        "type": "scatter",
                        "x": "YearsAtCompany",
                        "y": "MonthlyIncome",
                        "aggregation": "none",
                        "description": "Income and tenure relationship",
                    },
                ],
                "table": {
                    "dataset": "employees.csv",
                    "title": "Employee detail",
                    "columns": ["Department", "Attrition", "MonthlyIncome"],
                },
            }
        ],
        "theme": {"accent": "#2563eb", "background": "#f4f7fb"},
        "source_notes": [],
    }


class OutputRoutingTests(unittest.TestCase):
    def test_menu_is_printed_before_selection_and_allows_three_attempts(self) -> None:
        printed: list[str] = []
        answers = iter(["x", "no", "2"])
        selected = prompt_output_path(input_fn=lambda _: next(answers), print_fn=printed.append)
        self.assertIs(selected, OutputPath.HTML_DASHBOARD)
        self.assertIn("BI Dashboard (HTML)", printed[0])
        self.assertIn("self-contained HTML", printed[0])

    def test_route_aliases_and_legacy_power_bi_checkpoint_alias(self) -> None:
        self.assertIs(coerce_output_path("1"), OutputPath.ANALYTICS_REPORT)
        self.assertIs(coerce_output_path("html"), OutputPath.HTML_DASHBOARD)
        self.assertIs(coerce_output_path("power_bi"), OutputPath.HTML_DASHBOARD)

    def test_cli_selects_route_before_loading_environment_credentials(self) -> None:
        order: list[str] = []
        with patch.object(cli, "prompt_output_path", side_effect=lambda: order.append("route") or OutputPath.HTML_DASHBOARD), patch.object(
            cli, "load_runtime_config", side_effect=lambda: order.append("credentials") or _config()
        ), patch.object(cli, "run_terminal_workflow", return_value=0), patch.object(cli, "setup_logging"):
            self.assertEqual(cli.main(), 0)
        self.assertEqual(order, ["route", "credentials"])

    def test_noninteractive_route_is_required(self) -> None:
        with self.assertRaises(TypeError):
            run_non_interactive_workflow(_config(), [Path("data.csv")])


class BranchIsolationTests(unittest.TestCase):
    def _cached_orchestrator(self, root: Path, output_path: OutputPath) -> MultiAgentOrchestrator:
        orchestrator = MultiAgentOrchestrator(_config(), workspace=root, output_path=output_path)
        orchestrator.workflow_state["csv_data"] = {"employees.csv": _frame()}
        orchestrator.workflow_state["analysis_results"] = {"execution_status": "success"}
        orchestrator.workflow_state["agent_outputs"] = {
            "data_understander": {},
            "market_researcher": {},
            "planner": {},
            "final_code": "analysis_summary = {}",
            "business_translator": {},
            "decision_maker": {},
        }
        return orchestrator

    def test_dashboard_branch_never_invokes_pdf_or_powerpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            orchestrator = self._cached_orchestrator(Path(tmp), OutputPath.HTML_DASHBOARD)
            result = {
                "project": str(orchestrator.run_dir / "dashboard" / "Employee"),
                "html": str(orchestrator.run_dir / "dashboard" / "Employee" / "dashboard.html"),
                "qa_receipt": str(orchestrator.run_dir / "dashboard" / "Employee" / "dashboard_qa_receipt.json"),
            }
            with patch("analytics_workflow.html_dashboard.workflow.HTMLDashboardWorkflow.run", return_value=result), patch.object(
                orchestrator, "_generate_pdf_export"
            ) as pdf, patch.object(orchestrator, "_generate_slide_export") as slides:
                state = orchestrator.execute_workflow()
            self.assertEqual(state["status"], "completed")
            pdf.assert_not_called()
            slides.assert_not_called()
            self.assertNotIn("pdf", state["generated_reports"])
            self.assertNotIn("slide_deck", state["generated_reports"])

    def test_dashboard_constructs_only_data_understander(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            orchestrator = MultiAgentOrchestrator(_config(), workspace=Path(tmp), output_path=OutputPath.HTML_DASHBOARD)
        self.assertEqual(set(orchestrator.agents), {"data_understander"})
        self.assertIsNone(orchestrator.brave_client)

    def test_dashboard_uses_four_stage_sequence(self) -> None:
        self.assertEqual(
            workflow_steps_for(OutputPath.HTML_DASHBOARD),
            ["Data Understander", "Dashboard Planning Agent", "HTML Dashboard Generator", "HTML Dashboard QA Agent"],
        )

    def test_analytics_branch_does_not_construct_html_dashboard_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            orchestrator = self._cached_orchestrator(Path(tmp), OutputPath.ANALYTICS_REPORT)
            with patch.object(orchestrator, "_analysis_output_issues", return_value=[]), patch(
                "analytics_workflow.pipeline_runtime.build_evidence_bundle", return_value={}
            ), patch("analytics_workflow.pipeline_runtime.validate_and_sanitize_workflow"), patch.object(
                orchestrator, "_generate_pdf_export"
            ), patch.object(orchestrator, "_generate_slide_export"), patch.object(
                orchestrator, "_validate_final_artifact_consistency"
            ), patch("analytics_workflow.html_dashboard.workflow.HTMLDashboardWorkflow") as dashboard:
                state = orchestrator.execute_workflow()
            self.assertEqual(state["status"], "completed")
            dashboard.assert_not_called()


class DashboardPlanningTests(unittest.TestCase):
    def test_planner_disables_reasoning_and_uses_full_structured_output_budget(self) -> None:
        client = Mock()
        client.chat_completion_json.return_value = _candidate_plan()
        agent = HTMLDashboardPlanningAgent(client, timeout_seconds=30)

        plan = agent.execute(
            {
                "csv_data": {"employees.csv": _frame()},
                "workflow_objective": {},
                "agent_outputs": {"data_understander": {}},
            }
        )

        self.assertEqual(plan["title"], "Employee Overview")
        self.assertEqual(client.chat_completion_json.call_args.kwargs["max_tokens"], 8000)
        self.assertEqual(client.chat_completion_json.call_args.kwargs["reasoning_effort"], "none")

    def test_invalid_fields_are_removed_and_pages_are_limited_to_two_charts(self) -> None:
        candidate = _candidate_plan()
        candidate["filters"].append({"dataset": "employees.csv", "field": "Missing", "label": "Bad"})
        candidate["pages"][0]["charts"].extend(candidate["pages"][0]["charts"] * 2)
        plan = normalize_dashboard_plan(candidate, {"employees.csv": _frame()}, {})
        self.assertEqual(plan["filters"], [{"dataset": "employees.csv", "field": "Department", "label": "Department"}])
        self.assertLessEqual(len(plan["pages"][0]["charts"]), 2)
        self.assertTrue(plan["kpis"])

    def test_fallback_plan_is_source_backed(self) -> None:
        plan = normalize_dashboard_plan({}, {"employees.csv": _frame()}, {})
        self.assertTrue(plan["pages"])
        self.assertTrue(plan["kpis"])
        self.assertTrue(all(item["dataset"] == "employees.csv" for item in plan["kpis"]))


class DashboardRenderingTests(unittest.TestCase):
    def test_self_contained_dashboard_renders_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = normalize_dashboard_plan(_candidate_plan(), {"employees.csv": _frame()}, {})
            output = root / "dashboard.html"
            receipt = render_dashboard(
                plan,
                {"employees.csv": _frame()},
                [{"name": "employees.csv", "rows": 4, "columns": 4, "copy": "data/employees.csv", "sha256": "abc"}],
                output,
            )
            qa = validate_dashboard_html(output, plan, {"employees.csv": _frame()})
            content = output.read_text(encoding="utf-8")
            self.assertTrue(receipt["self_contained"])
            self.assertTrue(qa["passed"], qa)
            self.assertIn("application/json", content)
            self.assertIn("applyFilters", content)
            self.assertIn(".axis{fill:none;stroke:#cad3df", content)
            self.assertNotRegex(content, r'<(?:script|link|img)[^>]+(?:src|href)=["\']https?://')

    def test_workflow_writes_html_receipts_and_no_report_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "employees.csv"
            _frame().to_csv(source, index=False)
            workflow = HTMLDashboardWorkflow(_config(), Mock(), "run-1", root, lambda *_: None)
            state = {
                "csv_data": {"employees.csv": _frame()},
                "workflow_objective": {},
                "agent_outputs": {"html_dashboard_plan": _candidate_plan()},
                "run_manifest": {"datasets": [{"name": "employees.csv", "path": str(source)}]},
            }
            result = workflow.run(state)
            self.assertTrue(Path(result["html"]).is_file())
            self.assertTrue(Path(result["qa_receipt"]).is_file())
            self.assertTrue(Path(result["success_receipt"]).is_file())
            self.assertFalse(any(Path(result["project"]).rglob("*.pdf")))
            self.assertFalse(any(Path(result["project"]).rglob("*.pptx")))
            self.assertFalse(any(Path(result["project"]).rglob("*.pbip")))

    def test_row_limit_fails_before_planning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workflow = HTMLDashboardWorkflow(_config(html_dashboard_max_rows=2), Mock(), "run-1", Path(tmp), lambda *_: None)
            with self.assertRaises(DashboardWorkflowError) as caught:
                workflow.run({"csv_data": {"employees.csv": _frame()}, "agent_outputs": {}, "run_manifest": {"datasets": []}})
            self.assertEqual(caught.exception.code, DashboardErrorCode.TOO_MANY_ROWS.value)

    def test_path_confinement_blocks_external_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dashboard"
            root.mkdir()
            self.assertEqual(confined_path(root, root / "inside"), (root / "inside").resolve())
            with self.assertRaises(DashboardWorkflowError):
                confined_path(root, Path(tmp) / "outside")


class CodexPowerBIToolingTests(unittest.TestCase):
    def test_power_bi_is_not_an_analytics_runtime_package(self) -> None:
        self.assertIsNone(importlib.util.find_spec("analytics_workflow.powerbi"))

    def test_project_local_power_bi_tools_and_skill_remain_installed(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertTrue((root / "tools" / "powerbi" / "package.json").is_file())
        self.assertTrue((root / "skills" / "powerbi-authoring" / "LICENSE").is_file())


if __name__ == "__main__":
    unittest.main()
