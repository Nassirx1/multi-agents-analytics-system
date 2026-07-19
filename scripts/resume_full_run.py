from __future__ import annotations

import argparse
import json
from pathlib import Path

from analytics_workflow.pipeline_runtime import resume_non_interactive_workflow
from analytics_workflow.runtime_config import load_runtime_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Resume a checkpointed analytics workflow run.")
    parser.add_argument("run_directory", type=Path)
    args = parser.parse_args()
    result = resume_non_interactive_workflow(
        load_runtime_config(),
        args.run_directory,
        step_callback=lambda number, name, status: print(
            f"STEP {number} {name}: {status}", flush=True
        ),
    )
    print(
        json.dumps(
            {
                "status": result.get("status"),
                "run_directory": result.get("run_manifest", {}).get("run_directory", ""),
                "generated_reports": result.get("generated_reports", {}),
                "failure": result.get("failure", {}),
            },
            indent=2,
            default=str,
        )
    )
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
