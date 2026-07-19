from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class RunCheckpoint:
    run_directory: Path
    manifest: dict[str, Any]
    agent_outputs: dict[str, Any]
    analysis_results: dict[str, Any]

    @property
    def dataset_paths(self) -> list[Path]:
        return [
            Path(str(item.get("path", "")))
            for item in self.manifest.get("datasets", []) or []
            if isinstance(item, dict) and item.get("path")
        ]


def load_run_checkpoint(run_directory: Path) -> RunCheckpoint:
    """Load the durable, JSON-only portion of a prior workflow run."""
    run_dir = Path(run_directory).resolve(strict=True)
    manifest = _read_json_object(run_dir / "run_manifest.json", required=True)
    outputs = _read_json_object(run_dir / "agent_outputs.json", required=True)
    analysis = _read_json_object(run_dir / "analysis_results.json", required=False)
    return RunCheckpoint(run_dir, manifest, outputs, analysis)


def _read_json_object(path: Path, *, required: bool) -> dict[str, Any]:
    if not path.is_file():
        if required:
            raise ValueError(f"A resumable run requires {path.name}.")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Run checkpoint {path.name} is invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Run checkpoint {path.name} must contain a JSON object.")
    return payload
