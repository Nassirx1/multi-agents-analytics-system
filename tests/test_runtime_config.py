import logging
import os
import tempfile
import unittest
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

    def test_load_runtime_config_reads_environment_keys_and_default_model(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_API_KEY": "openrouter-env-secret",
                "BRAVE_API_KEY": "brave-env-secret",
                "ANALYTICS_MODEL": "model-from-env",
            },
            clear=False,
        ):
            config = load_runtime_config()

        self.assertEqual(config.openrouter_api_key, "openrouter-env-secret")
        self.assertEqual(config.brave_search_api_key, "brave-env-secret")
        self.assertEqual(config.model_name, "model-from-env")

    def test_load_runtime_config_reports_missing_env_keys(self) -> None:
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "BRAVE_API_KEY": ""}, clear=False), patch(
            "analytics_workflow.runtime_config.PROJECT_ENV_PATH",
            PROJECT_ENV_PATH.with_name("missing-test.env"),
        ):
            with self.assertRaisesRegex(
                ValueError,
                r"^Missing OPENROUTER_API_KEY\. Set it in the environment, add it to \.env, or enter it at the CLI prompt\.$",
            ):
                load_runtime_config()

    def test_prompt_runtime_config_reads_project_env_before_prompting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = os.path.join(temp_dir, ".env")
            with open(env_path, "w", encoding="utf-8") as handle:
                handle.write("OPENROUTER_API_KEY=openrouter-dotenv-secret\n")
                handle.write("BRAVE_API_KEY=brave-dotenv-secret\n")
                handle.write("ANALYTICS_MODEL=model-from-dotenv\n")
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "BRAVE_API_KEY": ""}, clear=False), patch(
                "analytics_workflow.runtime_config.PROJECT_ENV_PATH",
                PROJECT_ENV_PATH.__class__(env_path),
            ):
                config = prompt_runtime_config(
                    input_fn=lambda _: "",
                    secret_input_fn=lambda _: self.fail("prompt should not be called"),
                )

        self.assertEqual(config.openrouter_api_key, "openrouter-dotenv-secret")
        self.assertEqual(config.brave_search_api_key, "brave-dotenv-secret")
        self.assertEqual(config.model_name, "model-from-dotenv")

    def test_prompt_runtime_config_prompts_for_missing_keys_without_dotenv(self) -> None:
        prompts = iter(["openrouter-prompt-secret", "brave-prompt-secret"])
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "BRAVE_API_KEY": ""}, clear=False), patch(
            "analytics_workflow.runtime_config.PROJECT_ENV_PATH",
            PROJECT_ENV_PATH.with_name("missing-test.env"),
        ):
            config = prompt_runtime_config(
                input_fn=lambda _: "",
                secret_input_fn=lambda _: next(prompts),
            )

        self.assertEqual(config.openrouter_api_key, "openrouter-prompt-secret")
        self.assertEqual(config.brave_search_api_key, "brave-prompt-secret")
        self.assertEqual(config.model_name, DEFAULT_MODEL)

    def test_cli_registers_runtime_config_before_running_workflow(self) -> None:
        config = build_runtime_config("openrouter-secret", "brave-secret", "model-x")
        with patch.object(cli, "prompt_runtime_config", return_value=config), patch.object(
            cli, "run_terminal_workflow", return_value=0
        ) as run_workflow, patch.object(cli, "setup_logging") as setup_logging:
            result = cli.main()

        self.assertEqual(result, 0)
        self.assertIs(get_active_runtime_config(), config)
        setup_logging.assert_called_once()
        run_workflow.assert_called_once()

    def test_cli_stops_before_workflow_when_config_is_missing(self) -> None:
        with patch.object(
            cli,
            "prompt_runtime_config",
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
