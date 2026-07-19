from __future__ import annotations

import argparse
import json
from pathlib import Path

from analytics_workflow.pipeline_runtime import resume_non_interactive_workflow, run_non_interactive_workflow
from analytics_workflow.runtime_config import load_runtime_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one dataset through the complete analytics workflow.")
    parser.add_argument("dataset", type=Path, nargs="?")
    parser.add_argument("--context", default="")
    parser.add_argument("--decision-tree-target", default="")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output-path",
        choices=("analytics_report", "html_dashboard"),
        default="analytics_report",
        help="Explicit workflow branch to exercise.",
    )
    parser.add_argument("--resume-run", type=Path, help="Resume an existing run checkpoint instead of starting a new run.")
    args = parser.parse_args()

    callback = lambda number, name, status: print(f"STEP {number} {name}: {status}", flush=True)
    if args.resume_run:
        result = resume_non_interactive_workflow(
            load_runtime_config(),
            args.resume_run,
            step_callback=callback,
        )
    else:
        if args.dataset is None:
            parser.error("dataset is required unless --resume-run is supplied")
        result = run_non_interactive_workflow(
            load_runtime_config(),
            [args.dataset],
            args.context,
            output_path=args.output_path,
            decision_tree_target_column=args.decision_tree_target,
            workspace=args.workspace,
            step_callback=callback,
        )
    summary = {
        "status": result.get("status"),
        "run_directory": result.get("run_manifest", {}).get("run_directory", ""),
        "generated_reports": result.get("generated_reports", {}),
        "failure": result.get("failure", {}),
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
