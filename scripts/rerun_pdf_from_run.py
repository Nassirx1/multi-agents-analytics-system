from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from analytics_workflow.reporting import generate_pdf_report
from analytics_workflow.run_checkpoints import load_run_checkpoint


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate only the PDF from a completed analytics run.")
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    checkpoint = load_run_checkpoint(args.run_directory)
    run_dir = checkpoint.run_directory
    inputs = checkpoint.manifest.get("workflow_inputs", {}) or {}
    workflow_state: dict[str, Any] = {
        "status": "completed",
        "agent_outputs": checkpoint.agent_outputs,
        "analysis_results": checkpoint.analysis_results,
        "saved_figures": checkpoint.analysis_results.get("figures_generated", []) or [],
        "user_data_description": inputs.get("user_data_description", ""),
        "decision_tree_target_column": inputs.get("decision_tree_target_column", ""),
        "workflow_objective": {"limitations": []},
        "run_manifest": checkpoint.manifest,
        "generated_reports": dict(checkpoint.manifest.get("reports", {}) or {}),
        "evidence_bundle": _read_json(run_dir / "evidence_bundle.json"),
        "quality_receipt": _read_json(run_dir / "quality_receipt.json"),
    }
    output = (args.output or (run_dir / "analytics_report_readable.pdf")).resolve()
    result = Path(generate_pdf_report(workflow_state, str(output))).resolve()

    receipt = workflow_state.get("pdf_quality_receipt", {}) or {}
    (run_dir / "pdf_quality_receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    delivery_dir = run_dir / "delivery"
    delivery_dir.mkdir(exist_ok=True)
    delivery_pdf = delivery_dir / "analytics_report.pdf"
    shutil.copy2(result, delivery_pdf)
    shutil.copy2(run_dir / "pdf_quality_receipt.json", delivery_dir / "pdf_quality_receipt.json")

    manifest = checkpoint.manifest
    manifest.setdefault("reports", {})["pdf"] = str(result)
    manifest["authoritative_pdf"] = {
        "path": str(result),
        "delivery_copy": str(delivery_pdf.resolve()),
        "valid": bool(receipt.get("valid")),
        "issues": receipt.get("issues", []),
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                "pdf": str(result),
                "delivery_pdf": str(delivery_pdf.resolve()),
                "page_count": receipt.get("page_count", 0),
                "issues": receipt.get("issues", []),
                "valid": receipt.get("valid", False),
            },
            indent=2,
        )
    )
    return 0 if receipt.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
