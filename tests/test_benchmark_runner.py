from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from benchmark_suite.reporting import render_benchmark_markdown, write_benchmark_receipts
from benchmark_suite.runner import BenchmarkRunner, BenchmarkStatus
from benchmark_suite.catalog import load_benchmark_catalog
from benchmark_suite.evaluators import BENCHMARK_EXECUTORS
from scripts.run_benchmark_suite import main as benchmark_cli_main


def _case(
    benchmark_id: str,
    *,
    target: str = "contract",
    routes: tuple[str, ...] = ("shared",),
    stages: tuple[str, ...] = ("Data Understander",),
    dimensions: tuple[str, ...] = (),
    timeout_seconds: float = 1.0,
    fixture: dict | None = None,
    expected: dict | None = None,
    hard_gates: tuple[str, ...] = (),
    deterministic: bool = True,
    credentials_required: tuple[str, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        id=benchmark_id,
        name=benchmark_id.rsplit(".", 2)[-2].replace("_", " ").title(),
        category="workflow",
        target=target,
        routes=routes,
        stages=stages,
        dimensions=dimensions,
        timeout_seconds=timeout_seconds,
        fixture=fixture or {},
        expected=expected or {},
        hard_gates=hard_gates,
        description="Deterministic test case",
        tags=("unit",),
        deterministic=deterministic,
        credentials_required=credentials_required,
    )


class BenchmarkAccountingTests(unittest.TestCase):
    def test_every_real_catalog_target_has_a_concrete_executor_and_is_accounted(self) -> None:
        cases = load_benchmark_catalog()
        targets = {case.target for case in cases}
        self.assertEqual(targets, set(BENCHMARK_EXECUTORS))

        def deterministic_contract(context):
            return {
                "passed": True,
                "scores": {dimension: 100.0 for dimension in context.dimensions},
                "gate_results": {gate: True for gate in context.hard_gates},
            }

        suite = BenchmarkRunner(default_executor=deterministic_contract).run(cases)
        self.assertEqual(set(suite.accounted_benchmark_ids), {case.id for case in cases})
        self.assertEqual(suite.missing_benchmark_ids, [])
        self.assertEqual(suite.counts["skip"], 0)
        self.assertTrue(suite.passed)

    def test_every_catalog_case_is_accounted_and_both_routes_expand(self) -> None:
        cases = (
            _case("maa.routes.isolation.v1", routes=("analytics_report", "html_dashboard")),
            _case("maa.agent.contract.v1", routes=("shared",)),
        )
        suite = BenchmarkRunner(executors={"contract": lambda _: {"passed": True}}).run(cases)
        self.assertTrue(suite.passed)
        self.assertEqual(suite.catalog_case_count, 2)
        self.assertEqual(suite.execution_count, 3)
        self.assertEqual(set(suite.accounted_benchmark_ids), {item.id for item in cases})
        self.assertEqual(suite.missing_benchmark_ids, [])
        self.assertEqual({result.status for result in suite.results}, {BenchmarkStatus.PASS.value})

    def test_filtered_cases_are_still_explicitly_accounted_as_skips(self) -> None:
        cases = (
            _case("maa.analytics.only.v1", routes=("analytics_report",)),
            _case("maa.dashboard.only.v1", routes=("html_dashboard",)),
        )
        suite = BenchmarkRunner(executors={"contract": lambda _: True}).run(cases, routes=("html_dashboard",))
        by_id = {item.benchmark_id: item for item in suite.results}
        self.assertEqual(by_id["maa.analytics.only.v1"].status, "skip")
        self.assertEqual(by_id["maa.analytics.only.v1"].skipped_reason, "filtered by route")
        self.assertEqual(by_id["maa.dashboard.only.v1"].status, "pass")
        self.assertEqual(suite.missing_benchmark_ids, [])

    def test_duplicate_ids_and_unknown_filters_fail_before_execution(self) -> None:
        runner = BenchmarkRunner(executors={"contract": lambda _: True})
        duplicate = _case("maa.duplicate.case.v1")
        with self.assertRaisesRegex(ValueError, "Duplicate benchmark"):
            runner.run((duplicate, duplicate))
        with self.assertRaisesRegex(ValueError, "Unknown benchmark"):
            runner.run((duplicate,), benchmark_ids=("maa.missing.case.v1",))


class BenchmarkReliabilityTests(unittest.TestCase):
    def test_timeout_is_a_failure_and_does_not_block_suite(self) -> None:
        def slow(_):
            time.sleep(0.15)
            return {"passed": True}

        started = time.perf_counter()
        suite = BenchmarkRunner(executors={"slow": slow}).run(
            (_case("maa.reliability.timeout.v1", target="slow", timeout_seconds=0.01),)
        )
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.12)
        self.assertFalse(suite.passed)
        self.assertEqual(suite.results[0].status, "fail")
        self.assertEqual(suite.results[0].failure_kind, "timeout")

    def test_executor_error_is_redacted_and_later_case_runs(self) -> None:
        def broken(_):
            raise RuntimeError("bad token super-secret")

        cases = (
            _case("maa.reliability.error.v1", target="broken"),
            _case("maa.reliability.after_error.v1", target="ok"),
        )
        runner = BenchmarkRunner(
            executors={"broken": broken, "ok": lambda _: True},
            environ={"OPENROUTER_API_KEY": "super-secret"},
        )
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "super-secret"}, clear=False):
            suite = runner.run(cases)
        self.assertEqual([item.status for item in suite.results], ["fail", "pass"])
        self.assertEqual(suite.results[0].failure_kind, "executor_error")
        self.assertNotIn("super-secret", " ".join(suite.results[0].diagnostics))

    def test_unknown_executor_and_malformed_observation_fail_closed(self) -> None:
        missing = BenchmarkRunner().run((_case("maa.executor.missing.v1", target="absent"),))
        malformed = BenchmarkRunner(executors={"bad": lambda _: {"scores": {}}}).run(
            (_case("maa.executor.malformed.v1", target="bad"),)
        )
        self.assertEqual(missing.results[0].failure_kind, "executor_not_found")
        self.assertEqual(malformed.results[0].failure_kind, "invalid_observation")

    def test_live_and_missing_credentials_are_explicit_skips(self) -> None:
        cases = (
            _case("maa.live.disabled.v1", deterministic=False),
            _case(
                "maa.live.credential.v1",
                deterministic=False,
                credentials_required=("OPENROUTER_API_KEY", "BRAVE_API_KEY"),
            ),
        )
        runner = BenchmarkRunner(executors={"contract": lambda _: True}, environ={})
        disabled = runner.run(cases)
        enabled = runner.run(cases, include_live=True)
        self.assertEqual([item.status for item in disabled.results], ["skip", "skip"])
        self.assertIn("live-only", disabled.results[0].skipped_reason)
        self.assertEqual([item.status for item in enabled.results], ["pass", "skip"])
        self.assertIn("OPENROUTER_API_KEY", enabled.results[1].skipped_reason)
        self.assertNotIn("super-secret", enabled.results[1].skipped_reason)


class BenchmarkContractTests(unittest.TestCase):
    def test_required_dimensions_and_hard_gates_fail_closed(self) -> None:
        case = _case(
            "maa.output.quality.v1",
            dimensions=("readability", "trustworthiness"),
            hard_gates=("evidence_traceability", "no_blank_visuals"),
        )
        suite = BenchmarkRunner(
            executors={
                "contract": lambda _: {
                    "passed": True,
                    "scores": {"readability": 80.0},
                    "gate_results": {"evidence_traceability": True},
                }
            }
        ).run((case,))
        result = suite.results[0]
        self.assertEqual(result.status, "fail")
        self.assertTrue(any("trustworthiness" in item for item in result.diagnostics))
        self.assertTrue(any("no_blank_visuals" in item for item in result.diagnostics))

    def test_html_route_rejects_report_artifacts_and_unrelated_agents(self) -> None:
        case = _case("maa.route.html_isolation.v1", routes=("html_dashboard",))
        suite = BenchmarkRunner(
            executors={
                "contract": lambda _: {
                    "passed": True,
                    "artifacts": ["runs/example/analytics_report.pdf"],
                    "calls": ["Data Understander", "Market Researcher"],
                }
            }
        ).run((case,))
        result = suite.results[0]
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.failure_kind, "route_isolation")
        self.assertGreaterEqual(len(result.diagnostics), 2)

    def test_analytics_route_rejects_dashboard_artifacts(self) -> None:
        case = _case("maa.route.report_isolation.v1", routes=("analytics_report",))
        suite = BenchmarkRunner(
            executors={
                "contract": lambda _: {
                    "passed": True,
                    "artifacts": ["runs/example/dashboard.html"],
                    "invocations": ["HTML Dashboard Generator"],
                }
            }
        ).run((case,))
        self.assertEqual(suite.results[0].failure_kind, "route_isolation")

    def test_context_contains_deep_copied_fixture_and_route(self) -> None:
        case = _case("maa.contract.context.v1", routes=("html_dashboard",), fixture={"nested": {"value": 1}})

        def inspect_context(context):
            context.fixture["nested"]["value"] = 2
            return {"passed": context.route == "html_dashboard"}

        suite = BenchmarkRunner(executors={"contract": inspect_context}).run((case,))
        self.assertTrue(suite.passed)
        self.assertEqual(case.fixture["nested"]["value"], 1)

    def test_decision_maker_contract_failure_does_not_invent_hard_gate_failures(self) -> None:
        case = next(item for item in load_benchmark_catalog() if item.target == "agent.decision_maker")
        suite = BenchmarkRunner().run((case,))
        result = suite.results[0]
        self.assertEqual(result.status, "fail")
        self.assertTrue(any("required_action_fields" in item for item in result.diagnostics))
        self.assertFalse(any("Failed hard gate" in item for item in result.diagnostics))


class BenchmarkReportingTests(unittest.TestCase):
    def _scored_suite(self, passed: bool = True):
        case = _case(
            "maa.output.executive.v1",
            dimensions=("readability", "visibility", "trustworthiness", "executive_suitability"),
        )
        scores = {name: 90.0 for name in case.dimensions}
        return BenchmarkRunner(executors={"contract": lambda _: {"passed": passed, "scores": scores}}).run((case,))

    def test_json_and_markdown_receipts_are_readable_and_structured(self) -> None:
        suite = self._scored_suite()
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_benchmark_receipts(suite, Path(tmp))
            payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
            markdown = Path(paths["markdown"]).read_text(encoding="utf-8")
        self.assertTrue(payload["passed"])
        self.assertEqual(payload["results"][0]["scores"]["readability"], 90.0)
        self.assertIn("## Quality Dimensions", markdown)
        self.assertIn("executive_suitability", markdown)
        self.assertIn("## Benchmark Results", markdown)

    def test_markdown_explains_skip_semantics(self) -> None:
        case = _case("maa.live.readability.v1", deterministic=False)
        suite = BenchmarkRunner(executors={"contract": lambda _: True}).run((case,))
        markdown = render_benchmark_markdown(suite)
        self.assertIn("explicit skip", markdown)
        self.assertIn("live-only", markdown)


class BenchmarkCliTests(unittest.TestCase):
    def test_cli_exit_codes_zero_for_pass_one_for_benchmark_failure(self) -> None:
        passing = BenchmarkRunner(executors={"contract": lambda _: True}).run((_case("maa.cli.pass.v1"),))
        failing = BenchmarkRunner(executors={"contract": lambda _: False}).run((_case("maa.cli.fail.v1"),))
        with tempfile.TemporaryDirectory() as tmp, patch(
            "scripts.run_benchmark_suite.run_default_catalog", return_value=passing
        ):
            self.assertEqual(benchmark_cli_main(["--output-dir", tmp]), 0)
        with tempfile.TemporaryDirectory() as tmp, patch(
            "scripts.run_benchmark_suite.run_default_catalog", return_value=failing
        ):
            self.assertEqual(benchmark_cli_main(["--output-dir", tmp]), 1)

    def test_cli_returns_two_for_catalog_or_execution_configuration_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "scripts.run_benchmark_suite.run_default_catalog", side_effect=ValueError("bad catalog")
        ):
            self.assertEqual(benchmark_cli_main(["--output-dir", tmp]), 2)


if __name__ == "__main__":
    unittest.main()
