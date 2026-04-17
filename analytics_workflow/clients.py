from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import requests


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

    def chat_completion(self, system_prompt: str, user_prompt: str, max_retries: int = 3) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 4000,
        }
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
                    return data["choices"][0]["message"]["content"].strip()
                self._logger.error("API error %s: %s", response.status_code, response.text[:200])
            except Exception as exc:
                self._logger.error("OpenRouter request failed on attempt %s: %s", attempt + 1, exc)
                if attempt == max_retries - 1:
                    self.cost_tracker.failed_calls += 1
                    raise
            time.sleep(min(2**attempt, 8))
        raise RuntimeError("OpenRouter request failed after retries.")

    def chat_completion_json(self, system_prompt: str, user_prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        prompt = (
            f"{user_prompt}\n\n"
            "Respond ONLY with valid JSON matching this schema:\n"
            f"{json.dumps(schema, indent=2)}"
        )
        raw = self.chat_completion(system_prompt, prompt)
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw.strip())
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            return {"raw_text": raw, "parse_error": str(exc)}


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
