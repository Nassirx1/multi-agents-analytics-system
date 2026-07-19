from __future__ import annotations

from pathlib import Path
import sys

from .clients import setup_logging
from .output_paths import OutputPath, prompt_output_path
from .pipeline_runtime import run_terminal_workflow
from .runtime_config import (
    load_runtime_config,
    register_runtime_config,
)


def main() -> int:
    try:
        output_path = prompt_output_path()
        config = load_runtime_config()
    except ValueError as exc:
        print(str(exc))
        return 1
    print(f"Python executable: {sys.executable}")
    print(f"CLI module: {Path(__file__).resolve()}")
    register_runtime_config(config)
    setup_logging()
    return run_terminal_workflow(config, Path.cwd(), output_path=output_path)
