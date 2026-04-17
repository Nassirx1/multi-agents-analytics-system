from __future__ import annotations

from pathlib import Path

from .pipeline_runtime import MultiAgentOrchestrator, run_terminal_workflow
from .runtime_config import RuntimeConfig


def run_workflow(config: RuntimeConfig, workspace: Path | None = None) -> int:
    return run_terminal_workflow(config, workspace or Path.cwd())


__all__ = ["MultiAgentOrchestrator", "run_workflow"]
