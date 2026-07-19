from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def build_execution_logs(manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    run_id = str(manifest.get("run_id", "unknown-run"))
    entries: list[dict[str, Any]] = []
    for index, metric in enumerate(manifest.get("step_metrics", []) or [], start=1):
        if not isinstance(metric, dict):
            continue
        status = str(metric.get("status", "failure")).lower()
        entries.append(
            {
                "task_id": f"{run_id}-step-{metric.get('step', index)}-{index}",
                "agent_id": str(metric.get("name", f"step-{index}")).lower().replace(" ", "_"),
                "task_type": "analytics_workflow_step",
                "task_description": str(metric.get("name", f"Workflow step {index}")),
                "start_time": metric.get("started_at"),
                "end_time": metric.get("ended_at"),
                "duration_ms": int(metric.get("duration_ms", 0) or 0),
                "status": "success" if status == "done" else "failure",
                "actions": [],
                "results": {"quality_score": 1.0 if status == "done" else 0.0},
                "tokens_used": {
                    "input_tokens": int(metric.get("prompt_tokens", 0) or 0),
                    "output_tokens": int(metric.get("completion_tokens", 0) or 0),
                    "total_tokens": int(metric.get("prompt_tokens", 0) or 0)
                    + int(metric.get("completion_tokens", 0) or 0),
                },
                "cost_usd": float(metric.get("estimated_cost_usd", 0) or 0),
                "error_details": None if status == "done" else {"error_type": "step_failure"},
                "tools_used": [],
                "retry_count": 0,
                "metadata": {"run_id": run_id, "step": metric.get("step")},
            }
        )
    return {"execution_logs": entries}


def main() -> int:
    parser = argparse.ArgumentParser(description="Export run telemetry for the agent-designer evaluator.")
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run_dir = args.run_directory.resolve()
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    payload = build_execution_logs(manifest)
    output = (args.output or (run_dir / "agent_execution_logs.json")).resolve()
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
