from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "deepseek/deepseek-v3.2"
PROJECT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    openrouter_api_key: str
    brave_search_api_key: str
    model_name: str = DEFAULT_MODEL
    structured_model_name: str = ""
    code_model_name: str = ""
    presentation_model_name: str = ""
    market_research_enabled: bool = True
    presentation_architect_enabled: bool = False
    agent_request_timeout_seconds: int = 180
    code_loop_request_timeout_seconds: int = 900
    presentation_agent_timeout_seconds: int = 900
    analysis_timeout_seconds: int = 120
    max_csv_bytes: int = 100 * 1024 * 1024
    max_csv_rows: int = 1_000_000
    max_csv_columns: int = 500
    share_sample_values_with_model: bool = False
    presentation_backend: str = "auto"
    powerpoint_mcp_command: str = "mcp-ppt"
    html_dashboard_stage_timeout_seconds: int = 300
    html_dashboard_max_rows: int = 25_000

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
    *,
    agent_request_timeout_seconds: int = 180,
    code_loop_request_timeout_seconds: int = 900,
    presentation_agent_timeout_seconds: int = 900,
    analysis_timeout_seconds: int = 120,
    max_csv_bytes: int = 100 * 1024 * 1024,
    max_csv_rows: int = 1_000_000,
    max_csv_columns: int = 500,
    share_sample_values_with_model: bool = False,
    presentation_backend: str = "auto",
    powerpoint_mcp_command: str = "mcp-ppt",
    structured_model_name: str = "",
    code_model_name: str = "",
    presentation_model_name: str = "",
    market_research_enabled: bool = True,
    presentation_architect_enabled: bool = False,
    html_dashboard_stage_timeout_seconds: int = 300,
    html_dashboard_max_rows: int = 25_000,
) -> RuntimeConfig:
    openrouter = _require_value(openrouter_api_key, "OpenRouter API key")
    brave = _require_value(brave_search_api_key, "Brave Search API key")
    selected_model = (model_name or "").strip() or DEFAULT_MODEL
    return RuntimeConfig(
        openrouter_api_key=openrouter,
        brave_search_api_key=brave,
        model_name=selected_model,
        structured_model_name=(structured_model_name or selected_model).strip(),
        code_model_name=(code_model_name or selected_model).strip(),
        presentation_model_name=(presentation_model_name or selected_model).strip(),
        market_research_enabled=bool(market_research_enabled),
        presentation_architect_enabled=bool(presentation_architect_enabled),
        agent_request_timeout_seconds=max(1, int(agent_request_timeout_seconds)),
        code_loop_request_timeout_seconds=max(1, int(code_loop_request_timeout_seconds)),
        presentation_agent_timeout_seconds=max(1, int(presentation_agent_timeout_seconds)),
        analysis_timeout_seconds=max(1, int(analysis_timeout_seconds)),
        max_csv_bytes=max(1, int(max_csv_bytes)),
        max_csv_rows=max(1, int(max_csv_rows)),
        max_csv_columns=max(1, int(max_csv_columns)),
        share_sample_values_with_model=bool(share_sample_values_with_model),
        presentation_backend=_presentation_backend(presentation_backend),
        powerpoint_mcp_command=(powerpoint_mcp_command or "mcp-ppt").strip(),
        html_dashboard_stage_timeout_seconds=max(1, int(html_dashboard_stage_timeout_seconds)),
        html_dashboard_max_rows=max(1, int(html_dashboard_max_rows)),
    )


def load_runtime_config() -> RuntimeConfig:
    env_values = _runtime_env_values()
    return build_runtime_config(
        openrouter_api_key=_require_env_value("OPENROUTER_API_KEY", env_values),
        brave_search_api_key=_require_env_value("BRAVE_API_KEY", env_values),
        model_name=env_values.get("ANALYTICS_MODEL", ""),
        agent_request_timeout_seconds=_env_int(env_values, "AGENT_REQUEST_TIMEOUT_SECONDS", 180),
        code_loop_request_timeout_seconds=_env_int(env_values, "CODE_LOOP_REQUEST_TIMEOUT_SECONDS", 900),
        presentation_agent_timeout_seconds=_env_int(env_values, "PRESENTATION_AGENT_TIMEOUT_SECONDS", 900),
        analysis_timeout_seconds=_env_int(env_values, "ANALYSIS_TIMEOUT_SECONDS", 120),
        max_csv_bytes=_env_int(env_values, "MAX_CSV_BYTES", 100 * 1024 * 1024),
        max_csv_rows=_env_int(env_values, "MAX_CSV_ROWS", 1_000_000),
        max_csv_columns=_env_int(env_values, "MAX_CSV_COLUMNS", 500),
        share_sample_values_with_model=_env_bool(env_values, "SHARE_SAMPLE_VALUES_WITH_MODEL", False),
        presentation_backend=env_values.get("PRESENTATION_BACKEND", "auto"),
        powerpoint_mcp_command=env_values.get("POWERPOINT_MCP_COMMAND", "mcp-ppt"),
        structured_model_name=env_values.get("STRUCTURED_ANALYTICS_MODEL", ""),
        code_model_name=env_values.get("CODE_ANALYTICS_MODEL", ""),
        presentation_model_name=env_values.get("PRESENTATION_ANALYTICS_MODEL", ""),
        market_research_enabled=_env_bool(env_values, "MARKET_RESEARCH_ENABLED", True),
        presentation_architect_enabled=_env_bool(env_values, "PRESENTATION_ARCHITECT_ENABLED", False),
        html_dashboard_stage_timeout_seconds=_env_int(env_values, "HTML_DASHBOARD_STAGE_TIMEOUT_SECONDS", 300),
        html_dashboard_max_rows=_env_int(env_values, "HTML_DASHBOARD_MAX_ROWS", 25_000),
    )


def prompt_runtime_config(**_: object) -> RuntimeConfig:
    """Backward-compatible name; credentials are loaded from the environment only."""
    return load_runtime_config()


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


_ACTIVE_CONFIG: ContextVar[RuntimeConfig | None] = ContextVar("analytics_runtime_config", default=None)


def register_runtime_config(config: RuntimeConfig | None) -> None:
    _ACTIVE_CONFIG.set(config)


def get_active_runtime_config() -> RuntimeConfig | None:
    return _ACTIVE_CONFIG.get()


def _runtime_env_values() -> dict[str, str]:
    values = _read_project_env(PROJECT_ENV_PATH)
    for name in (
        "OPENROUTER_API_KEY",
        "BRAVE_API_KEY",
        "ANALYTICS_MODEL",
        "STRUCTURED_ANALYTICS_MODEL",
        "CODE_ANALYTICS_MODEL",
        "PRESENTATION_ANALYTICS_MODEL",
        "MARKET_RESEARCH_ENABLED",
        "PRESENTATION_ARCHITECT_ENABLED",
        "AGENT_REQUEST_TIMEOUT_SECONDS",
        "CODE_LOOP_REQUEST_TIMEOUT_SECONDS",
        "PRESENTATION_AGENT_TIMEOUT_SECONDS",
        "ANALYSIS_TIMEOUT_SECONDS",
        "MAX_CSV_BYTES",
        "MAX_CSV_ROWS",
        "MAX_CSV_COLUMNS",
        "SHARE_SAMPLE_VALUES_WITH_MODEL",
        "PRESENTATION_BACKEND",
        "POWERPOINT_MCP_COMMAND",
        "HTML_DASHBOARD_STAGE_TIMEOUT_SECONDS",
        "HTML_DASHBOARD_MAX_ROWS",
    ):
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
        if key not in {
            "OPENROUTER_API_KEY",
            "BRAVE_API_KEY",
            "ANALYTICS_MODEL",
            "STRUCTURED_ANALYTICS_MODEL",
            "CODE_ANALYTICS_MODEL",
            "PRESENTATION_ANALYTICS_MODEL",
            "MARKET_RESEARCH_ENABLED",
            "PRESENTATION_ARCHITECT_ENABLED",
            "AGENT_REQUEST_TIMEOUT_SECONDS",
            "CODE_LOOP_REQUEST_TIMEOUT_SECONDS",
            "PRESENTATION_AGENT_TIMEOUT_SECONDS",
            "ANALYSIS_TIMEOUT_SECONDS",
            "MAX_CSV_BYTES",
            "MAX_CSV_ROWS",
            "MAX_CSV_COLUMNS",
            "SHARE_SAMPLE_VALUES_WITH_MODEL",
            "PRESENTATION_BACKEND",
            "POWERPOINT_MCP_COMMAND",
            "HTML_DASHBOARD_STAGE_TIMEOUT_SECONDS",
            "HTML_DASHBOARD_MAX_ROWS",
        }:
            continue
        values[key] = raw_value.strip().strip('"').strip("'")
    return values


def _env_int(values: dict[str, str], name: str, default: int) -> int:
    raw = values.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero.")
    return value


def _env_bool(values: dict[str, str], name: str, default: bool) -> bool:
    raw = values.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false.")


def _env_nonnegative_int(values: dict[str, str], name: str, default: int) -> int:
    raw = values.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if value < 0:
        raise ValueError(f"{name} must be zero or greater.")
    return value


def _require_env_value(name: str, values: dict[str, str]) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise ValueError(f"Missing {name}. Set it in the process, user, or machine environment, or project .env.")
    return value


def _require_value(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} is required.")
    return normalized


def _presentation_backend(value: str) -> str:
    normalized = (value or "auto").strip().lower()
    if normalized not in {"auto", "powerpoint_mcp", "python"}:
        raise ValueError("PRESENTATION_BACKEND must be auto, powerpoint_mcp, or python.")
    return normalized
