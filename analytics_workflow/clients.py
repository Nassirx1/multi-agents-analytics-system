from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import requests

from .runtime_config import get_active_runtime_config, redact_secrets


class _SecretRedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        config = get_active_runtime_config()
        if config is None:
            return True
        try:
            rendered = record.getMessage()
        except Exception:
            return True
        redacted = redact_secrets(rendered, config)
        if redacted != rendered:
            record.msg = redacted
            record.args = ()
        return True


def setup_logging(run_id: str | None = None) -> logging.Logger:
    run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"analytics_run_{run_id}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)-24s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.FileHandler(log_filename), logging.StreamHandler()],
        force=True,
    )
    redaction_filter = _SecretRedactingFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(redaction_filter)
    logger = logging.getLogger("SYSTEM")
    logger.info("Run ID: %s | Log: %s", run_id, log_filename)
    return logger


SYSTEM_LOGGER = setup_logging()


@dataclass
class CostTracker:
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_calls: int = 0
    failed_calls: int = 0
    cost_per_1k: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            "openai/gpt-5.5": {"prompt": 0.005, "completion": 0.03},
            "deepseek/deepseek-v3.2": {"prompt": 0.00027, "completion": 0.0011},
            "openai/gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
            "default": {"prompt": 0.001, "completion": 0.002},
        }
    )

    def record(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_calls += 1

    def estimated_cost_usd(self) -> float:
        rates = self.cost_per_1k.get(self.model, self.cost_per_1k["default"])
        return (
            (self.prompt_tokens / 1000) * rates["prompt"]
            + (self.completion_tokens / 1000) * rates["completion"]
        )

    def report(self) -> str:
        total = self.prompt_tokens + self.completion_tokens
        return (
            "=== COST SUMMARY =================================\n"
            f"  Model         : {self.model}\n"
            f"  API Calls     : {self.total_calls} ({self.failed_calls} failed)\n"
            f"  Prompt tokens : {self.prompt_tokens:,}\n"
            f"  Completion    : {self.completion_tokens:,}\n"
            f"  Total tokens  : {total:,}\n"
            f"  Est. cost     : ${self.estimated_cost_usd():.4f} USD\n"
            "=================================================="
        )


class SharedContextStore:
    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self._logger = logging.getLogger("SharedContextStore")

    def set(self, key: str, value: Any, source_agent: str = "system") -> None:
        self._store[key] = value
        self._logger.debug("SET %s by %s", key, source_agent)

    def get(self, key: str, default: Any = None) -> Any:
        return self._store.get(key, default)


class OpenRouterClient:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = "https://openrouter.ai/api/v1"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://localhost",
                "X-Title": "Multi-Agent Analytics System",
            }
        )
        self.cost_tracker = CostTracker(model=model)
        self._logger = logging.getLogger("OpenRouterClient")

    _MAX_TOKENS_LADDER = (4000, 6000, 8000)

    def chat_completion(self, system_prompt: str, user_prompt: str, max_retries: int = 3) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.7,
            "max_tokens": self._MAX_TOKENS_LADDER[0],
        }
        last_empty_reason = ""
        ladder_index = 0
        for attempt in range(max_retries):
            try:
                response = self.session.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    timeout=60,
                )
                if response.status_code == 200:
                    data = response.json()
                    usage = data.get("usage", {})
                    self.cost_tracker.record(
                        usage.get("prompt_tokens", 0),
                        usage.get("completion_tokens", 0),
                    )
                    content, empty_reason = self._extract_message_content(data)
                    if content:
                        return content
                    last_empty_reason = empty_reason
                    self._logger.error(
                        "OpenRouter returned empty content on attempt %s (model=%s): %s",
                        attempt + 1,
                        self.model,
                        empty_reason,
                    )
                    if empty_reason.startswith("finish_reason=length") and ladder_index < len(self._MAX_TOKENS_LADDER) - 1:
                        ladder_index += 1
                        payload["max_tokens"] = self._MAX_TOKENS_LADDER[ladder_index]
                else:
                    self._logger.error("API error %s: %s", response.status_code, response.text[:200])
            except Exception as exc:
                self._logger.error("OpenRouter request failed on attempt %s: %s", attempt + 1, exc)
                if attempt == max_retries - 1:
                    self.cost_tracker.failed_calls += 1
                    raise
            time.sleep(min(2**attempt, 8))
        self.cost_tracker.failed_calls += 1
        detail = f" Last reason: {last_empty_reason}." if last_empty_reason else ""
        raise RuntimeError(f"OpenRouter request failed after {max_retries} retries.{detail}")

    @staticmethod
    def _extract_message_content(data: dict[str, Any]) -> tuple[str, str]:
        if not isinstance(data, dict):
            return "", f"non-dict response body: {type(data).__name__}"
        if data.get("error"):
            err = data["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            return "", f"upstream error: {msg}"
        choices = data.get("choices") or []
        if not choices:
            return "", "response had no choices"
        first = choices[0] or {}
        message = first.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            parts = [
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") in (None, "text", "output_text")
            ]
            content = "".join(parts)
        if not isinstance(content, str) or not content.strip():
            finish = first.get("finish_reason") or first.get("native_finish_reason") or "unknown"
            refusal = message.get("refusal")
            reason = f"finish_reason={finish}"
            if refusal:
                reason += f", refusal={str(refusal)[:120]}"
            return "", reason
        return content.strip(), ""

    def chat_completion_json(self, system_prompt: str, user_prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        prompt = (
            f"{user_prompt}\n\n"
            "Respond ONLY with valid JSON matching this schema:\n"
            f"{json.dumps(schema, indent=2)}"
        )
        raw = self.chat_completion(system_prompt, prompt)
        cleaned = self._strip_code_fences(raw)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            self._logger.error(
                "chat_completion_json failed to parse JSON (model=%s): %s | raw=%s",
                self.model,
                exc,
                cleaned[:200],
            )
            sharpened_prompt = (
                f"{prompt}\n\n"
                "Your previous response was not valid JSON. Reply with ONLY a JSON object "
                "matching the schema. No fences, no prose."
            )
            retry_raw = self.chat_completion(system_prompt, sharpened_prompt)
            retry_cleaned = self._strip_code_fences(retry_raw)
            try:
                return json.loads(retry_cleaned)
            except json.JSONDecodeError as retry_exc:
                self._logger.error(
                    "chat_completion_json retry still invalid (model=%s): %s",
                    self.model,
                    retry_exc,
                )
                return {"raw_text": retry_cleaned, "parse_error": str(retry_exc)}

    @staticmethod
    def _strip_code_fences(raw: str) -> str:
        stripped = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        return re.sub(r"\s*```$", "", stripped.strip())


class BraveSearchClient:
    def __init__(self, api_key: str, max_queries: int = 3) -> None:
        self.api_key = api_key
        self.max_queries = max_queries
        self.query_count = 0
        self.base_url = "https://api.search.brave.com/res/v1/web/search"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": api_key,
            }
        )
        self._logger = logging.getLogger("BraveSearchClient")

    def search(self, query: str, count: int = 3) -> list[dict[str, str]]:
        if self.query_count >= self.max_queries:
            return []
        self.query_count += 1
        try:
            response = self.session.get(
                self.base_url,
                params={"q": query, "count": count, "mkt": "en-US", "text_format": "Raw"},
                timeout=15,
            )
            if response.status_code != 200:
                return []
            data = response.json()
            return [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "description": item.get("description", ""),
                }
                for item in data.get("web", {}).get("results", [])
            ]
        except Exception as exc:
            self._logger.error("Brave search failed: %s", exc)
            return []
