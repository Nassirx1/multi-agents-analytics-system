from __future__ import annotations

from pathlib import Path
import sys

from .pipeline_runtime import run_terminal_workflow
from .runtime_config import prompt_runtime_config, register_runtime_config


def main() -> int:
    print(f"Python executable: {sys.executable}")
    print(f"CLI module: {Path(__file__).resolve()}")
    config = prompt_runtime_config()
    register_runtime_config(config)
    return run_terminal_workflow(config, Path.cwd())
