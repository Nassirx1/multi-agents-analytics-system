from __future__ import annotations

import argparse
import json
from pathlib import Path

from analytics_workflow.presentation_backends import inspect_presentation
from analytics_workflow.reporting import inspect_pdf_report
from analytics_workflow.slides.deck_spec import DeckSpec


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit final PDF/PPTX trust, readability, and evidence consistency.")
    parser.add_argument("run_directory", type=Path)
    args = parser.parse_args()
    run_dir = args.run_directory.resolve()
    manifest = _read_json(run_dir / "run_manifest.json")
    evidence = _read_json(run_dir / "evidence_bundle.json")
    quality = _read_json(run_dir / "quality_receipt.json")
    consistency = _read_json(run_dir / "final_output_consistency.json")
    slide_plan_payload = _read_json(run_dir / "slide_plan.json")
    slide_plan = DeckSpec.from_any(slide_plan_payload) if slide_plan_payload else None

    pdf_path = Path(str((manifest.get("reports", {}) or {}).get("pdf") or run_dir / "analytics_report.pdf"))
    pptx_path = Path(str((manifest.get("reports", {}) or {}).get("slide_deck") or run_dir / "analytics_report.pptx"))
    pdf = inspect_pdf_report(str(pdf_path))
    slides = inspect_presentation(str(pptx_path), expected_deck=slide_plan)
    expected_hash = str(evidence.get("bundle_hash", ""))
    slide_hash = str(((slide_plan_payload.get("metadata", {}) or {}).get("evidence_bundle_hash", "")))
    report_outline = _read_json(run_dir / "report_outline.json")
    pdf_hash = str(report_outline.get("evidence_bundle_hash", ""))
    hash_match = bool(expected_hash) and expected_hash == slide_hash == pdf_hash
    delivery = run_dir / "delivery"
    delivery_complete = all(
        (delivery / name).is_file()
        for name in ("analytics_report.pdf", "analytics_report.pptx", "evidence_bundle.json", "quality_receipt.json", "delivery_manifest.json")
    )
    result = {
        "run": str(run_dir),
        "quality_status": quality.get("status") or manifest.get("quality_status"),
        "evidence_bundle_hash": expected_hash,
        "pdf": pdf,
        "slides": slides.to_dict(),
        "consistency": consistency,
        "evidence_hash_match": hash_match,
        "delivery_complete": delivery_complete,
    }
    result["valid"] = bool(pdf.get("valid")) and slides.valid and hash_match and delivery_complete
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["valid"] else 1


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
