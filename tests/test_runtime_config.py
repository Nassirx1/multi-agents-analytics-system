import logging
import os
import tempfile
import unittest

from analytics_workflow.clients import setup_logging
from analytics_workflow.runtime_config import (
    DEFAULT_MODEL,
    build_runtime_config,
    mask_secret,
    redact_secrets,
    register_runtime_config,
)


class RuntimeConfigTests(unittest.TestCase):
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
