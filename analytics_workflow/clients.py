from __future__ import annotations

import json
import base64
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
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


class _SafeFileHandler(logging.FileHandler):
    """Ignore late writes after a temporary log directory has been removed."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except FileNotFoundError:
            return


def setup_logging(run_id: str | None = None) -> logging.Logger:
    run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"analytics_run_{run_id}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)-24s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=[_SafeFileHandler(log_filename), logging.StreamHandler()],
        force=True,
    )
    redaction_filter = _SecretRedactingFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(redaction_filter)
    logger = logging.getLogger("SYSTEM")
    logger.info("Run ID: %s | Log: %s", run_id, log_filename)
    return logger


@dataclass
class CostTracker:
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_calls: int = 0
    failed_calls: int = 0
    per_model_usage: dict[str, dict[str, int]] = field(default_factory=dict)
    cost_per_1k: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            "openai/gpt-5.5": {"prompt": 0.005, "completion": 0.03},
            "deepseek/deepseek-v3.2": {"prompt": 0.00027, "completion": 0.0011},
            "openai/gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
            "default": {"prompt": 0.001, "completion": 0.002},
        }
    )

    def record(self, prompt_tokens: int, completion_tokens: int, model: str | None = None) -> None:
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_calls += 1
        selected_model = (model or self.model or "default").strip()
        usage = self.per_model_usage.setdefault(
            selected_model, {"prompt_tokens": 0, "completion_tokens": 0, "api_calls": 0}
        )
        usage["prompt_tokens"] += int(prompt_tokens)
        usage["completion_tokens"] += int(completion_tokens)
        usage["api_calls"] += 1

    def estimated_cost_usd(self) -> float:
        if self.per_model_usage:
            return sum(
                (usage["prompt_tokens"] / 1000)
                * self.cost_per_1k.get(model, self.cost_per_1k["default"])["prompt"]
                + (usage["completion_tokens"] / 1000)
                * self.cost_per_1k.get(model, self.cost_per_1k["default"])["completion"]
                for model, usage in self.per_model_usage.items()
            )
        rates = self.cost_per_1k.get(self.model, self.cost_per_1k["default"])
        return (
            (self.prompt_tokens / 1000) * rates["prompt"]
            + (self.completion_tokens / 1000) * rates["completion"]
        )

    def snapshot(self) -> dict[str, Any]:
        """Return machine-readable usage for manifests and step telemetry."""
        return {
            "model": self.model,
            "api_calls": int(self.total_calls),
            "failed_calls": int(self.failed_calls),
            "prompt_tokens": int(self.prompt_tokens),
            "completion_tokens": int(self.completion_tokens),
            "total_tokens": int(self.prompt_tokens + self.completion_tokens),
            "estimated_cost_usd": round(float(self.estimated_cost_usd()), 6),
            "per_model_usage": {model: dict(usage) for model, usage in self.per_model_usage.items()},
        }

    def report(self) -> str:
        total = self.prompt_tokens + self.completion_tokens
        model_lines = ""
        if self.per_model_usage:
            model_lines = "".join(
                f"  {model:<14}: {usage['api_calls']} calls, "
                f"{usage['prompt_tokens'] + usage['completion_tokens']:,} tokens\n"
                for model, usage in sorted(self.per_model_usage.items())
            )
        return (
            "=== COST SUMMARY =================================\n"
            f"  Model         : {self.model}\n"
            f"  API Calls     : {self.total_calls} ({self.failed_calls} failed)\n"
            f"  Prompt tokens : {self.prompt_tokens:,}\n"
            f"  Completion    : {self.completion_tokens:,}\n"
            f"  Total tokens  : {total:,}\n"
            f"  Est. cost     : ${self.estimated_cost_usd():.4f} USD\n"
            f"{model_lines}"
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
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        request_timeout_seconds: int = 180,
        code_loop_timeout_seconds: int = 900,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = "https://openrouter.ai/api/v1"
        self.session = requests.Session()
        self.request_timeout_seconds = max(1, int(request_timeout_seconds))
        self.code_loop_timeout_seconds = max(1, int(code_loop_timeout_seconds))
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://localhost",
                "X-Title": "Multi-Agent Analytics System",
            }
        )
        self.cost_tracker = cost_tracker or CostTracker(model=model)
        self._logger = logging.getLogger("OpenRouterClient")
        self.last_reasoning: dict[str, Any] = {}
        self.reasoning_history: list[dict[str, Any]] = []

    _MAX_TOKENS_LADDER = (4000, 6000, 8000)
    _REASONING_HISTORY_LIMIT = 20

    def chat_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        max_retries: int = 3,
        max_tokens: int | None = None,
        timeout_seconds: int | None = None,
        response_format: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        ladder_index = 0
        if max_tokens is not None:
            ladder_index = max(
                (index for index, token_limit in enumerate(self._MAX_TOKENS_LADDER) if token_limit <= max_tokens),
                default=0,
            )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.7,
            "max_tokens": max_tokens or self._MAX_TOKENS_LADDER[ladder_index],
        }
        reasoning_config = (
            {"effort": reasoning_effort, "exclude": True}
            if reasoning_effort is not None
            else self._reasoning_config()
        )
        if reasoning_config:
            payload["reasoning"] = reasoning_config
        if response_format:
            payload["response_format"] = response_format
        last_empty_reason = ""
        for attempt in range(max_retries):
            retry_delay = min(2**attempt, 8)
            try:
                self._logger.info(
                    "OpenRouter request attempt %s/%s (model=%s, max_tokens=%s)",
                    attempt + 1,
                    max_retries,
                    self.model,
                    payload["max_tokens"],
                )
                response = self.session.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    timeout=timeout_seconds or self.request_timeout_seconds,
                )
                if response.status_code == 200:
                    data = response.json()
                    usage = data.get("usage", {})
                    self.cost_tracker.record(
                        usage.get("prompt_tokens", 0),
                        usage.get("completion_tokens", 0),
                        self.model,
                    )
                    self._record_reasoning(data)
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
                    affordable_tokens = self._affordable_token_limit(response.text)
                    if response.status_code == 402 and affordable_tokens:
                        capped_tokens = max(512, affordable_tokens - 128)
                        if capped_tokens < int(payload["max_tokens"]):
                            payload["max_tokens"] = capped_tokens
                            self._logger.warning(
                                "Retrying OpenRouter request with max_tokens=%s after provider credit limit.",
                                payload["max_tokens"],
                            )
                    elif response.status_code == 429 or response.status_code in {408, 409, 425} or response.status_code >= 500:
                        retry_after = str(response.headers.get("Retry-After", "")).strip()
                        try:
                            retry_delay = min(max(float(retry_after), 0.0), 60.0) if retry_after else retry_delay
                        except ValueError:
                            retry_delay = retry_delay
                    else:
                        raise RuntimeError(
                            f"OpenRouter request is not retryable ({response.status_code}): {response.text[:300]}"
                        )
            except Exception as exc:
                self._logger.error("OpenRouter request failed on attempt %s: %s", attempt + 1, exc)
                if isinstance(exc, RuntimeError) and "not retryable" in str(exc):
                    self.cost_tracker.failed_calls += 1
                    raise
                if attempt == max_retries - 1:
                    self.cost_tracker.failed_calls += 1
                    raise
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
        self.cost_tracker.failed_calls += 1
        detail = f" Last reason: {last_empty_reason}." if last_empty_reason else ""
        raise RuntimeError(f"OpenRouter request failed after {max_retries} retries.{detail}")

    def chat_completion_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        timeout_seconds: int | None = None,
        max_tokens: int = 4000,
    ) -> dict[str, Any]:
        """Return one assistant message for an Agent-9-only tool-calling loop."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.2,
            "max_tokens": min(max_tokens, 2000),
        }
        response = self.session.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            timeout=timeout_seconds or self.request_timeout_seconds,
        )

        if response.status_code != 200:
            self.cost_tracker.failed_calls += 1
            raise RuntimeError(
                f"OpenRouter tool request failed ({response.status_code}): {response.text[:300]}"
            )
        data = response.json()
        usage = data.get("usage", {})
        self.cost_tracker.record(
            usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), self.model
        )
        self._record_reasoning(data)
        choices = data.get("choices") or []
        message = (choices[0] or {}).get("message") if choices else None
        if not isinstance(message, dict):
            raise RuntimeError("OpenRouter tool response did not contain an assistant message.")
        return message

    def chat_completion_multimodal_json(
        self,
        system_prompt: str,
        user_prompt: str,
        image_paths: list[Path],
        schema: dict[str, Any],
        *,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Send local screenshots to a vision-capable model and require schema-shaped JSON."""
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": f"{user_prompt}\n\nReturn only JSON matching this schema:\n{json.dumps(schema, indent=2)}",
            }
        ]
        for path in image_paths:
            resolved = Path(path).resolve(strict=True)
            mime = "image/png" if resolved.suffix.lower() == ".png" else "image/jpeg"
            encoded = base64.b64encode(resolved.read_bytes()).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "temperature": 0.1,
            "max_tokens": 4000,
            "response_format": {"type": "json_object"},
        }
        last_error = ""
        for attempt in range(3):
            response = self.session.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                timeout=timeout_seconds or self.request_timeout_seconds,
            )
            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                if response.status_code not in {408, 409, 425, 429} and response.status_code < 500:
                    break
                if attempt < 2:
                    time.sleep(min(2**attempt, 4))
                continue
            data = response.json()
            usage = data.get("usage", {})
            self.cost_tracker.record(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), self.model)
            raw, reason = self._extract_message_content(data)
            if not raw:
                last_error = reason
                continue
            try:
                parsed = json.loads(self._strip_code_fences(raw))
            except json.JSONDecodeError as exc:
                last_error = str(exc)
                continue
            issues = self._schema_issues(parsed, schema)
            if not issues:
                return parsed
            last_error = "; ".join(issues[:8])
        self.cost_tracker.failed_calls += 1
        raise RuntimeError(f"Vision QA returned no valid JSON after 3 attempts: {last_error}")

    @staticmethod
    def _affordable_token_limit(response_text: str) -> int | None:
        match = re.search(r"can only afford\s+(\d+)", response_text or "", flags=re.IGNORECASE)
        if not match:
            return None
        try:
            return max(int(match.group(1)), 0)
        except ValueError:
            return None

    def _reasoning_config(self) -> dict[str, Any]:
        normalized_model = self.model.lower().strip()
        if normalized_model.startswith("deepseek/deepseek-v3.2"):
            return {"enabled": True, "exclude": False}
        return {}

    def _record_reasoning(self, data: dict[str, Any]) -> None:
        reasoning = self._extract_message_reasoning(data)
        if not reasoning:
            return
        self.last_reasoning = reasoning
        self.reasoning_history.append(reasoning)
        if len(self.reasoning_history) > self._REASONING_HISTORY_LIMIT:
            self.reasoning_history = self.reasoning_history[-self._REASONING_HISTORY_LIMIT :]
        self._logger.info(
            "OpenRouter returned reasoning fields for model=%s: %s",
            self.model,
            ", ".join(sorted(reasoning)),
        )

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

    @staticmethod
    def _extract_message_reasoning(data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {}
        choices = data.get("choices") or []
        if not choices:
            return {}
        first = choices[0] or {}
        message = first.get("message") or {}
        reasoning: dict[str, Any] = {}
        for field_name in ("reasoning", "reasoning_details", "reasoning_content"):
            value = message.get(field_name)
            if value not in (None, "", []):
                reasoning[field_name] = value
        return reasoning

    def chat_completion_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        *,
        timeout_seconds: int | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        prompt = (
            f"{user_prompt}\n\n"
            "Respond ONLY with valid JSON matching this schema:\n"
            f"{json.dumps(schema, indent=2)}"
        )
        timeout_kwargs: dict[str, Any] = {"response_format": {"type": "json_object"}}
        if timeout_seconds is not None:
            timeout_kwargs["timeout_seconds"] = timeout_seconds
        if max_tokens is not None:
            timeout_kwargs["max_tokens"] = max_tokens
        if reasoning_effort is not None:
            timeout_kwargs["reasoning_effort"] = reasoning_effort
        raw = self.chat_completion(system_prompt, prompt, **timeout_kwargs)
        cleaned = self._strip_code_fences(raw)
        first_error = ""
        try:
            parsed = json.loads(cleaned)
            schema_issues = self._schema_issues(parsed, schema)
            if not schema_issues:
                return parsed
            first_error = "schema validation failed: " + "; ".join(schema_issues[:8])
        except json.JSONDecodeError as exc:
            first_error = str(exc)
            self._logger.error(
                "chat_completion_json failed to parse JSON (model=%s): %s | raw=%s",
                self.model,
                exc,
                cleaned[:200],
            )
        if first_error.startswith("schema validation"):
            self._logger.error("chat_completion_json %s (model=%s)", first_error, self.model)
        sharpened_prompt = (
            f"{prompt}\n\n"
            "Your previous response was not valid JSON matching the required schema. Reply with ONLY a JSON object "
            "matching the schema. No fences, no prose.\n"
            f"Validation error: {first_error[:600]}"
        )
        retry_raw = self.chat_completion(system_prompt, sharpened_prompt, **timeout_kwargs)
        retry_cleaned = self._strip_code_fences(retry_raw)
        try:
            retry_parsed = json.loads(retry_cleaned)
        except json.JSONDecodeError as retry_exc:
            self._logger.error(
                "chat_completion_json retry still invalid (model=%s): %s",
                self.model,
                retry_exc,
            )
            return self._final_json_repair(
                system_prompt,
                prompt,
                retry_cleaned,
                schema,
                timeout_kwargs,
                f"invalid JSON: {retry_exc}",
            )
        retry_issues = self._schema_issues(retry_parsed, schema)
        if retry_issues:
            detail = "; ".join(retry_issues[:8])
            self._logger.error("chat_completion_json retry failed schema validation (model=%s): %s", self.model, detail)
            return self._final_json_repair(
                system_prompt,
                prompt,
                retry_cleaned,
                schema,
                timeout_kwargs,
                f"schema mismatch: {detail}",
            )
        return retry_parsed

    def _final_json_repair(
        self,
        system_prompt: str,
        original_prompt: str,
        invalid_text: str,
        schema: dict[str, Any],
        timeout_kwargs: dict[str, Any],
        error_detail: str,
    ) -> dict[str, Any]:
        repair_prompt = (
            f"{original_prompt}\n\n"
            "Repair the invalid candidate below. Return one complete JSON object only. "
            "Do not copy invalid bare tokens, comments, ellipses, or trailing prose.\n"
            f"ERROR: {error_detail[:600]}\n"
            f"INVALID CANDIDATE:\n{invalid_text[:6000]}"
        )
        final_raw = self.chat_completion(system_prompt, repair_prompt, **timeout_kwargs)
        final_cleaned = self._strip_code_fences(final_raw)
        try:
            final_parsed = json.loads(final_cleaned)
        except json.JSONDecodeError as exc:
            self._logger.error("chat_completion_json final repair invalid (model=%s): %s", self.model, exc)
            raise RuntimeError(f"Model returned invalid JSON after final repair: {exc}") from exc
        final_issues = self._schema_issues(final_parsed, schema)
        if final_issues:
            detail = "; ".join(final_issues[:8])
            self._logger.error("chat_completion_json final repair schema mismatch (model=%s): %s", self.model, detail)
            raise RuntimeError(f"Model JSON failed schema validation after final repair: {detail}")
        return final_parsed

    @classmethod
    def _schema_issues(cls, value: Any, schema: Any, path: str = "$") -> list[str]:
        issues: list[str] = []
        if isinstance(schema, dict):
            if not isinstance(value, dict):
                return [f"{path} must be an object"]
            placeholder_items = [
                (key, nested_schema)
                for key, nested_schema in schema.items()
                if isinstance(key, str) and key.startswith("<") and key.endswith(">")
            ]
            regular_items = [
                (key, nested_schema)
                for key, nested_schema in schema.items()
                if not (isinstance(key, str) and key.startswith("<") and key.endswith(">"))
            ]
            for key, nested_schema in regular_items:
                if key not in value:
                    issues.append(f"{path}.{key} is required")
                    continue
                issues.extend(cls._schema_issues(value[key], nested_schema, f"{path}.{key}"))
            for placeholder, nested_schema in placeholder_items:
                if not value:
                    issues.append(f"{path} must contain at least one {placeholder} entry")
                    continue
                for actual_key, actual_value in value.items():
                    issues.extend(cls._schema_issues(actual_value, nested_schema, f"{path}.{actual_key}"))
            return issues
        if isinstance(schema, list):
            if not isinstance(value, list):
                return [f"{path} must be an array"]
            if schema:
                for index, item in enumerate(value):
                    issues.extend(cls._schema_issues(item, schema[0], f"{path}[{index}]"))
            return issues
        if isinstance(schema, str):
            descriptor = schema.strip().lower()
            if "|" in descriptor:
                options = tuple(part.strip() for part in descriptor.split("|") if part.strip())
                type_options = {"string", "int", "integer", "number", "float", "bool", "boolean", "null"}
                if options and not set(options).issubset(type_options):
                    if not isinstance(value, str):
                        return [f"{path} must be a string"]
                    if value.strip().lower() not in options:
                        return [f"{path} must be one of: {', '.join(options)}"]
                    return []
                if options:
                    matches_type = {
                        "string": lambda candidate: isinstance(candidate, str),
                        "int": lambda candidate: isinstance(candidate, int) and not isinstance(candidate, bool),
                        "integer": lambda candidate: isinstance(candidate, int) and not isinstance(candidate, bool),
                        "number": lambda candidate: isinstance(candidate, (int, float)) and not isinstance(candidate, bool),
                        "float": lambda candidate: isinstance(candidate, (int, float)) and not isinstance(candidate, bool),
                        "bool": lambda candidate: isinstance(candidate, bool),
                        "boolean": lambda candidate: isinstance(candidate, bool),
                        "null": lambda candidate: candidate is None,
                    }
                    if any(matches_type[option](value) for option in options):
                        return []
                    return [f"{path} must match one of: {', '.join(options)}"]
            if value is None and "null" in descriptor:
                return []
            if "integer" in descriptor or descriptor in {"int", "integer"}:
                if not isinstance(value, int) or isinstance(value, bool):
                    return [f"{path} must be an integer"]
            elif "number" in descriptor or "float" in descriptor:
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    return [f"{path} must be a number"]
            elif "boolean" in descriptor or descriptor == "bool":
                if not isinstance(value, bool):
                    return [f"{path} must be a boolean"]
            elif not isinstance(value, str):
                return [f"{path} must be a string"]
        return issues

    @staticmethod
    def _strip_code_fences(raw: str) -> str:
        stripped = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        return re.sub(r"\s*```$", "", stripped.strip())


class BraveSearchClient:
    def __init__(self, api_key: str, max_queries: int = 3) -> None:
        self.api_key = api_key
        self.max_queries = max_queries
        self.query_count = 0
        self._query_lock = threading.Lock()
        self._cache: dict[tuple[str, int], list[dict[str, str]]] = {}
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
        cache_key = (query.strip(), int(count))
        with self._query_lock:
            if cache_key in self._cache:
                return [dict(item) for item in self._cache[cache_key]]
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
            results = [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "description": item.get("description", ""),
                }
                for item in data.get("web", {}).get("results", [])
            ]
            with self._query_lock:
                self._cache[cache_key] = [dict(item) for item in results]
            return results
        except Exception as exc:
            self._logger.error("Brave search failed: %s", exc)
            return []
