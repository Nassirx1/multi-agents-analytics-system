from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

DEFAULT_MODEL = "deepseek/deepseek-v3.2"

PromptFn = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    openrouter_api_key: str
    brave_search_api_key: str
    model_name: str = DEFAULT_MODEL

    def masked_openrouter_key(self) -> str:
        return mask_secret(self.openrouter_api_key)

    def masked_brave_search_key(self) -> str:
        return mask_secret(self.brave_search_api_key)

    def openrouter_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.openrouter_api_key}"}

    def brave_search_headers(self) -> dict[str, str]:
        return {"X-Subscription-Token": self.brave_search_api_key}

    def masked_summary(self) -> dict[str, str]:
        return {
            "openrouter_api_key": self.masked_openrouter_key(),
            "brave_search_api_key": self.masked_brave_search_key(),
            "model_name": self.model_name,
        }


def build_runtime_config(
    openrouter_api_key: str,
    brave_search_api_key: str,
    model_name: str | None = None,
) -> RuntimeConfig:
    openrouter = _require_value(openrouter_api_key, "OpenRouter API key")
    brave = _require_value(brave_search_api_key, "Brave Search API key")
    selected_model = (model_name or "").strip() or DEFAULT_MODEL
    return RuntimeConfig(
        openrouter_api_key=openrouter,
        brave_search_api_key=brave,
        model_name=selected_model,
    )


def prompt_runtime_config(
    input_fn: PromptFn = input,
) -> RuntimeConfig:
    openrouter_api_key = _prompt_required(input_fn, "OpenRouter API key: ")
    brave_search_api_key = _prompt_required(input_fn, "Brave Search API key: ")
    model_name = input_fn(
        f"Model override (press Enter for {DEFAULT_MODEL}): "
    ).strip()
    return build_runtime_config(
        openrouter_api_key=openrouter_api_key,
        brave_search_api_key=brave_search_api_key,
        model_name=model_name,
    )


def mask_secret(value: str, visible_chars: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible_chars * 2:
        return "*" * len(value)
    masked_length = len(value) - (visible_chars * 2)
    return f"{value[:visible_chars]}{'*' * masked_length}{value[-visible_chars:]}"


def redact_secrets(text: str, config: RuntimeConfig) -> str:
    redacted = text
    for secret in (config.openrouter_api_key, config.brave_search_api_key):
        if secret:
            redacted = redacted.replace(secret, mask_secret(secret))
    return redacted


def _prompt_required(prompt_fn: PromptFn, message: str) -> str:
    while True:
        value = prompt_fn(message).strip()
        if value:
            return value
        print("Value is required.")


def _require_value(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} is required.")
    return normalized
