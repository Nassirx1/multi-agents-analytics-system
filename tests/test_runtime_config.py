import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import analytics_workflow.cli as cli
from analytics_workflow.clients import setup_logging
from analytics_workflow.runtime_config import (
    DEFAULT_MODEL,
    PROJECT_ENV_PATH,
    build_runtime_config,
    load_runtime_config,
    mask_secret,
    prompt_runtime_config,
    redact_secrets,
    get_active_runtime_config,
    register_runtime_config,
)


class RuntimeConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        register_runtime_config(None)

    def test_blank_model_override_uses_default(self) -> None:
        config = build_runtime_config("openrouter-secret", "brave-secret", "   ")
        self.assertEqual(config.model_name, DEFAULT_MODEL)

    def test_role_specific_models_default_and_can_be_overridden(self) -> None:
        defaulted = build_runtime_config("openrouter-secret", "brave-secret", "model-base")
        self.assertEqual(defaulted.structured_model_name, "model-base")
        self.assertEqual(defaulted.code_model_name, "model-base")
        self.assertEqual(defaulted.presentation_model_name, "model-base")
        self.assertTrue(defaulted.market_research_enabled)
        self.assertFalse(defaulted.presentation_architect_enabled)
        routed = build_runtime_config(
            "openrouter-secret",
            "brave-secret",
            "model-base",
            structured_model_name="model-fast",
            code_model_name="model-code",
            presentation_model_name="model-slides",
        )
        self.assertEqual(routed.structured_model_name, "model-fast")
        self.assertEqual(routed.code_model_name, "model-code")
        self.assertEqual(routed.presentation_model_name, "model-slides")

    def test_headers_use_expected_authentication_shape(self) -> None:
        config = build_runtime_config("openrouter-secret", "brave-secret", "model-x")
        self.assertEqual(
            config.openrouter_headers(),
            {"Authorization": "Bearer openrouter-secret"},
        )
        self.assertEqual(
            config.brave_search_headers(),
            {"X-Subscription-Token": "brave-secret"},
        )

    def test_mask_secret_masks_short_and_long_values(self) -> None:
        self.assertEqual(mask_secret("abcd"), "****")
        self.assertEqual(mask_secret("abcdefghijkl"), "abcd****ijkl")

    def test_redact_secrets_replaces_full_values(self) -> None:
        config = build_runtime_config("openrouter-secret", "brave-secret")
        text = "OpenRouter=openrouter-secret Brave=brave-secret"
        self.assertEqual(
            redact_secrets(text, config),
            "OpenRouter=open*********cret Brave=brav****cret",
        )

    def test_missing_secrets_raise_error(self) -> None:
        with self.assertRaises(ValueError):
            build_runtime_config("", "brave-secret")
        with self.assertRaises(ValueError):
            build_runtime_config("openrouter-secret", "")

    def test_presentation_backend_is_validated(self) -> None:
        self.assertEqual(
            build_runtime_config("openrouter", "brave", presentation_backend="POWERPOINT_MCP").presentation_backend,
            "powerpoint_mcp",
        )
        with self.assertRaisesRegex(ValueError, "PRESENTATION_BACKEND"):
            build_runtime_config("openrouter", "brave", presentation_backend="unknown")

    def test_load_runtime_config_reads_environment_keys_and_default_model(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_API_KEY": "openrouter-env-secret",
                "BRAVE_API_KEY": "brave-env-secret",
                "ANALYTICS_MODEL": "model-from-env",
                "PRESENTATION_BACKEND": "python",
                "POWERPOINT_MCP_COMMAND": "custom-mcp-ppt",
                "PRESENTATION_AGENT_TIMEOUT_SECONDS": "901",
            },
            clear=False,
        ):
            config = load_runtime_config()

        self.assertEqual(config.openrouter_api_key, "openrouter-env-secret")
        self.assertEqual(config.brave_search_api_key, "brave-env-secret")
        self.assertEqual(config.model_name, "model-from-env")
        self.assertEqual(config.presentation_backend, "python")
        self.assertEqual(config.powerpoint_mcp_command, "custom-mcp-ppt")
        self.assertEqual(config.presentation_agent_timeout_seconds, 901)

    def test_load_runtime_config_reports_missing_env_keys(self) -> None:
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "BRAVE_API_KEY": ""}, clear=False), patch(
            "analytics_workflow.runtime_config.PROJECT_ENV_PATH",
            PROJECT_ENV_PATH.with_name("missing-test.env"),
        ):
            with self.assertRaisesRegex(
                ValueError,
                r"^Missing OPENROUTER_API_KEY\. Set it in the process, user, or machine environment, or project \.env\.$",
            ):
                load_runtime_config()

    def test_load_runtime_config_reads_project_env_credentials_without_prompting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "OPENROUTER_API_KEY=openrouter-file-secret\nBRAVE_API_KEY=brave-file-secret\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "BRAVE_API_KEY": ""}, clear=False), patch(
                "analytics_workflow.runtime_config.PROJECT_ENV_PATH",
                env_path,
            ):
                config = load_runtime_config()

        self.assertEqual(config.openrouter_api_key, "openrouter-file-secret")
        self.assertEqual(config.brave_search_api_key, "brave-file-secret")

    def test_prompt_runtime_config_is_environment_only_compatibility_alias(self) -> None:
        with patch.dict(
            os.environ,
            {"OPENROUTER_API_KEY": "openrouter-env-secret", "BRAVE_API_KEY": "brave-env-secret"},
            clear=False,
        ):
            config = prompt_runtime_config(
                input_fn=lambda _: (_ for _ in ()).throw(AssertionError("input must not be called")),
                secret_input_fn=lambda _: (_ for _ in ()).throw(AssertionError("secret input must not be called")),
            )
        self.assertEqual(config.openrouter_api_key, "openrouter-env-secret")
        self.assertEqual(config.brave_search_api_key, "brave-env-secret")

    def test_prompt_runtime_config_never_falls_back_to_prompting(self) -> None:
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "BRAVE_API_KEY": ""}, clear=False), patch(
            "analytics_workflow.runtime_config.PROJECT_ENV_PATH",
            PROJECT_ENV_PATH.with_name("missing-test.env"),
        ):
            with self.assertRaisesRegex(ValueError, "Missing OPENROUTER_API_KEY"):
                prompt_runtime_config(secret_input_fn=lambda _: "must-not-be-used")

    def test_cli_registers_runtime_config_before_running_workflow(self) -> None:
        config = build_runtime_config("openrouter-secret", "brave-secret", "model-x")
        with patch.object(cli, "prompt_output_path", return_value=cli.OutputPath.ANALYTICS_REPORT), patch.object(
            cli, "load_runtime_config", return_value=config
        ), patch.object(
            cli, "run_terminal_workflow", return_value=0
        ) as run_workflow, patch.object(cli, "setup_logging") as setup_logging:
            result = cli.main()

        self.assertEqual(result, 0)
        self.assertIs(get_active_runtime_config(), config)
        setup_logging.assert_called_once()
        run_workflow.assert_called_once()

    def test_cli_stops_before_workflow_when_config_is_missing(self) -> None:
        with patch.object(cli, "prompt_output_path", return_value=cli.OutputPath.ANALYTICS_REPORT), patch.object(
            cli,
            "load_runtime_config",
            side_effect=ValueError("Brave Search API key is required."),
        ), patch.object(cli, "run_terminal_workflow") as run_workflow, patch(
            "builtins.print"
        ) as print_mock, patch.object(cli, "setup_logging") as setup_logging:
            result = cli.main()

        self.assertEqual(result, 1)
        print_mock.assert_any_call("Brave Search API key is required.")
        setup_logging.assert_not_called()
        run_workflow.assert_not_called()


class RedactingLogFilterTests(unittest.TestCase):
    def tearDown(self) -> None:
        register_runtime_config(None)

    def test_log_file_masks_registered_secrets(self) -> None:
        config = build_runtime_config("openrouter-supersecret", "brave-supersecret")
        register_runtime_config(config)
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)
            try:
                setup_logging(run_id="redact_test")
                logging.getLogger("RedactTest").error(
                    "leaked openrouter-supersecret in message"
                )
                for handler in logging.getLogger().handlers:
                    handler.flush()
                with open(
                    os.path.join(tmpdir, "analytics_run_redact_test.log"),
                    encoding="utf-8",
                ) as fh:
                    contents = fh.read()
            finally:
                logging.shutdown()
                os.chdir(original_cwd)
        self.assertNotIn("openrouter-supersecret", contents)
        self.assertIn(mask_secret("openrouter-supersecret"), contents)

    def test_filter_is_noop_when_no_config_registered(self) -> None:
        register_runtime_config(None)
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)
            try:
                setup_logging(run_id="noop_test")
                logging.getLogger("RedactTest").error("plain message no secret")
                for handler in logging.getLogger().handlers:
                    handler.flush()
                with open(
                    os.path.join(tmpdir, "analytics_run_noop_test.log"),
                    encoding="utf-8",
                ) as fh:
                    contents = fh.read()
            finally:
                logging.shutdown()
                os.chdir(original_cwd)
        self.assertIn("plain message no secret", contents)


if __name__ == "__main__":
    unittest.main()
