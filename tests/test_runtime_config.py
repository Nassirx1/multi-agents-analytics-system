import unittest

from analytics_workflow.runtime_config import (
    DEFAULT_MODEL,
    build_runtime_config,
    mask_secret,
    redact_secrets,
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


if __name__ == "__main__":
    unittest.main()
