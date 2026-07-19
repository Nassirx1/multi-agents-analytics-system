from __future__ import annotations

from pathlib import Path

from .pipeline_runtime import MultiAgentOrchestrator, run_non_interactive_workflow, run_terminal_workflow
from .runtime_config import RuntimeConfig
from .output_paths import OutputPath


def run_workflow(
    config: RuntimeConfig,
    workspace: Path | None = None,
    *,
    output_path: OutputPath | str,
) -> int:
    return run_terminal_workflow(config, workspace or Path.cwd(), output_path=output_path)


__all__ = ["MultiAgentOrchestrator", "run_non_interactive_workflow", "run_workflow"]
