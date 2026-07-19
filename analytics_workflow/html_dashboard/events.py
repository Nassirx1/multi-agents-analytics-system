from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from ..runtime_config import RuntimeConfig, redact_secrets


class DashboardEventLogger:
    def __init__(self, path: Path, run_id: str, config: RuntimeConfig) -> None:
        self.path = path
        self.run_id = run_id
        self.config = config
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, stage: str, result: str, **fields: Any) -> None:
        event = {
            "timestamp": datetime.now().astimezone().isoformat(),
            "run_id": self.run_id,
            "route": "html_dashboard",
            "stage": stage,
            "result": result,
            **fields,
        }
        safe = json.loads(redact_secrets(json.dumps(event, default=str), self.config))
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(safe, sort_keys=True) + "\n")
