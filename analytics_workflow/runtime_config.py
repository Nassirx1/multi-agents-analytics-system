from __future__ import annotations

import os
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path
from typing import Callable

DEFAULT_MODEL = "deepseek/deepseek-v3.2"
PROJECT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


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


def load_runtime_config() -> RuntimeConfig:
    env_values = _runtime_env_values()
    return build_runtime_config(
        openrouter_api_key=_require_env_value("OPENROUTER_API_KEY", env_values),
        brave_search_api_key=_require_env_value("BRAVE_API_KEY", env_values),
        model_name=env_values.get("ANALYTICS_MODEL", ""),
    )


def prompt_runtime_config(
    *,
    input_fn: Callable[[str], str] = input,
    secret_input_fn: Callable[[str], str] | None = None,
) -> RuntimeConfig:
    secret_prompt = secret_input_fn or getpass
    env_values = _runtime_env_values()
    openrouter_key = env_values.get("OPENROUTER_API_KEY", "")
    brave_key = env_values.get("BRAVE_API_KEY", "")
    model_name = env_values.get("ANALYTICS_MODEL", "") or DEFAULT_MODEL

    if not openrouter_key:
        openrouter_key = secret_prompt("OpenRouter API key: ").strip()
    if not brave_key:
        brave_key = secret_prompt("Brave Search API key: ").strip()
    model_override = input_fn(f"Model [{model_name}]: ").strip()
    return build_runtime_config(openrouter_key, brave_key, model_override or model_name)


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


_ACTIVE_CONFIG: RuntimeConfig | None = None


def register_runtime_config(config: RuntimeConfig | None) -> None:
    global _ACTIVE_CONFIG
    _ACTIVE_CONFIG = config


def get_active_runtime_config() -> RuntimeConfig | None:
    return _ACTIVE_CONFIG


def _runtime_env_values() -> dict[str, str]:
    values = _read_project_env(PROJECT_ENV_PATH)
    for name in ("OPENROUTER_API_KEY", "BRAVE_API_KEY", "ANALYTICS_MODEL"):
        value = os.getenv(name, "").strip()
        if value:
            values[name] = value
    return values


def _read_project_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in lines:
        clean = line.strip()
        if not clean or clean.startswith("#") or "=" not in clean:
            continue
        key, raw_value = clean.split("=", 1)
        key = key.strip()
        if key not in {"OPENROUTER_API_KEY", "BRAVE_API_KEY", "ANALYTICS_MODEL"}:
            continue
        values[key] = raw_value.strip().strip('"').strip("'")
    return values


def _require_env_value(name: str, values: dict[str, str]) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise ValueError(f"Missing {name}. Set it in the environment, add it to .env, or enter it at the CLI prompt.")
    return value


def _require_value(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} is required.")
    return normalized
