from __future__ import annotations

from pathlib import Path

from .pipeline_runtime import run_terminal_workflow
from .runtime_config import prompt_runtime_config


def main() -> int:
    config = prompt_runtime_config()
    return run_terminal_workflow(config, Path.cwd())
